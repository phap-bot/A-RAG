"""Web-facing application service for the current A-RAG checkpoints.

The React application owns presentation only. This module is the server-side
adapter between its ``/v1`` contract and the Zone 1 ``ingestion_pipeline``
StateGraph:

    upload bytes -> ingestion_pipeline graph -> ordered runtime events -> store

Authentication users may also be persisted through the configured Neo4j
repository when enabled. API metadata and local development records are
checkpointed below ``.runtime/web``; source bytes are stored as separate files
for preview and download. Upload and ingestion are separate commands: upload
persists the original bytes as ``uploaded``; an explicit start command runs the
parser, provenance-safe chunking, embeddings, and Neo4j indexing. No LLM is
allowed to rewrite source text in this path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from src.core.config import logger, settings
from src.core.exceptions import GraphDBError, IngestionError
from src.ingestion.parser.models import ContentChunk, ParsedDocument
from src.ingestion.pipeline import (
    IngestionPipelineRuntime,
    execute_ingestion_pipeline,
    initial_ingestion_state,
)
from src.retrieval.embeddings import EmbeddingProvider, build_embedding_provider
from src.retrieval.neo4j_repository import Neo4jRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative_path(value: str, fallback: str) -> str:
    """Normalize a browser relative path and reject traversal segments."""
    candidate = (value or fallback).replace("\\", "/").strip("/")
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("relative_path must be a safe relative file path")
    return "/".join(parts)


def _short_preview(document: ParsedDocument, limit: int = 24000) -> str:
    """Render existing parsed element content without rewriting it."""
    text = "\n\n".join(element.content for element in document.elements)
    return text[:limit]


_PASSWORD_HASHER = PasswordHasher()


@dataclass
class UserRecord:
    user_id: str
    email: str
    display_name: str
    password_hash: str
    role: str = "member"
    is_active: bool = True
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def as_response(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }


@dataclass
class WorkspaceRecord:
    workspace_id: str
    name: str
    description: str
    owner_id: str
    created_at: str = field(default_factory=_utc_now)


@dataclass
class JobRecord:
    job_id: str
    workspace_id: str
    document_id: str
    status: str = "uploaded"
    started_at: str | None = None
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    flow_stage: str | None = None
    flow_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StoredDocument:
    document_id: str
    workspace_id: str
    name: str
    source_path: str
    content_type: str
    source_bytes: bytes
    parsed_document: ParsedDocument | None = None
    chunks: list[ContentChunk] = field(default_factory=list)
    status: str = "uploaded"
    uploaded_at: str = field(default_factory=_utc_now)
    job_id: str | None = None
    error: dict[str, Any] | None = None
    editable: bool = True
    upload_action: str = "created"
    storage_path: str | None = None


class WebApplicationService:
    """Minimal persistent-boundary adapter consumed by the imported web app."""

    def __init__(
        self,
        root: str | Path = ".runtime/web",
        *,
        neo4j_repository: Neo4jRepository | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.users: dict[str, UserRecord] = {}
        self.users_by_email: dict[str, str] = {}
        self.workspaces: dict[str, WorkspaceRecord] = {}
        self.workspace_members: dict[str, dict[str, str]] = {}
        self.documents: dict[str, StoredDocument] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.chat_sessions: dict[str, dict[str, Any]] = {}
        self.feedback: dict[str, dict[str, Any]] = {}
        self.evaluation_jobs: dict[str, dict[str, Any]] = {}
        self._state_path = self.root / "service_state.json"
        self.neo4j_repository = neo4j_repository
        self.embedding_provider = embedding_provider
        self._neo4j_prepared = False
        self._ingestion_executor = ThreadPoolExecutor(
            max_workers=settings.ingestion_worker_count,
            thread_name_prefix="ingestion-worker",
        )
        self._evaluation_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="evaluation-worker",
        )
        self._executor_closed = False
        self._load_state()

    def _load_state(self) -> None:
        """Restore local API metadata; indexed vectors/graph remain in Neo4j."""
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.users = {
                key: UserRecord(**value)
                for key, value in payload.get("users", {}).items()
            }
            self.users_by_email = {
                user.email: user.user_id for user in self.users.values()
            }
            self.workspaces = {
                key: WorkspaceRecord(**value)
                for key, value in payload.get("workspaces", {}).items()
            }
            self.workspace_members = payload.get("workspace_members", {})
            self.jobs = {
                key: JobRecord(**value)
                for key, value in payload.get("jobs", {}).items()
            }
            for job in self.jobs.values():
                if job.status == "processing":
                    job.status = "failed"
                    job.finished_at = _utc_now()
                    job.error = {
                        "error_type": "InterruptedOnRestart",
                        "message": "Ingestion was interrupted when the API process stopped.",
                        "details": {"resume_supported": False},
                    }
            self.documents = {}
            for key, value in payload.get("documents", {}).items():
                document_data = dict(value)
                source_path = str(document_data.get("source_path", ""))
                relative = _safe_relative_path(
                    source_path.removeprefix("uploads/"),
                    str(document_data.get("name", "document.bin")),
                )
                stored_path = document_data.get("storage_path")
                if stored_path:
                    source_file = (self.root / _safe_relative_path(str(stored_path), relative)).resolve()
                    source_file.relative_to(self.root.resolve())
                else:
                    source_file = (self.root / "workspaces" / str(document_data["workspace_id"]) / relative).resolve()
                    source_file.relative_to((self.root / "workspaces").resolve())
                source_bytes = source_file.read_bytes() if source_file.is_file() else b""
                parsed_data = document_data.pop("parsed_document", None)
                chunks_data = document_data.pop("chunks", [])
                document_data["source_bytes"] = source_bytes
                document_data["parsed_document"] = ParsedDocument.model_validate(parsed_data) if parsed_data else None
                document_data["chunks"] = [ContentChunk.model_validate(item) for item in chunks_data]
                document = StoredDocument(**document_data)
                if document.status == "processing":
                    document.status = "failed"
                    interrupted_job = self.jobs.get(document.job_id or "")
                    document.error = interrupted_job.error if interrupted_job else {
                        "error_type": "InterruptedOnRestart",
                        "message": "Ingestion was interrupted when the API process stopped.",
                        "details": {"resume_supported": False},
                    }
                self.documents[key] = document
            self.chat_sessions = payload.get("chat_sessions", {})
            self.feedback = payload.get("feedback", {})
            self.evaluation_jobs = payload.get("evaluation_jobs", {})
            for job in self.evaluation_jobs.values():
                if job.get("status") in {"queued", "running"} or job.get("score_status") == "scoring":
                    for row in job.get("rows", []):
                        if row.get("status") in {"queued", "running"}:
                            row.update({
                                "status": "failed",
                                "error_code": "InterruptedOnRestart",
                                "error_message": "Evaluation was interrupted when the API process stopped.",
                            })
                    job["completed_rows"] = sum(row.get("status") == "completed" for row in job.get("rows", []))
                    job["failed_rows"] = sum(row.get("status") == "failed" for row in job.get("rows", []))
                    job.update({
                        "status": "failed" if job["failed_rows"] == job.get("total_rows", 0) else "completed",
                        "score_status": "failed",
                        "error_message": "Evaluation was interrupted when the API process stopped.",
                        "updated_at": _utc_now(),
                        "finished_at": _utc_now(),
                    })
        except Exception:
            logger.exception(f"Could not restore local API state from {self._state_path}")

    def persist_state(self) -> None:
        """Atomically checkpoint local user/workspace/chat/job/evaluation metadata."""
        try:
            with self._lock:
                payload = {
                    "version": 1,
                    "users": {key: vars(value) for key, value in self.users.items()},
                    "workspaces": {key: vars(value) for key, value in self.workspaces.items()},
                    "workspace_members": self.workspace_members,
                    "documents": {
                        key: {
                            **vars(value),
                            "source_bytes": None,
                            "parsed_document": value.parsed_document.model_dump(mode="json") if value.parsed_document else None,
                            "chunks": [chunk.model_dump(mode="json") for chunk in value.chunks],
                        }
                        for key, value in self.documents.items()
                    },
                    "jobs": {key: vars(value) for key, value in self.jobs.items()},
                    "chat_sessions": self.chat_sessions,
                    "feedback": self.feedback,
                    "evaluation_jobs": self.evaluation_jobs,
                }
                temporary = self._state_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, self._state_path)
        except Exception:
            logger.exception(f"Could not persist local API state to {self._state_path}")

    def _get_neo4j_repository(self) -> Neo4jRepository | None:
        """Return the explicitly enabled repository, prepared exactly once."""
        if self.neo4j_repository is None and not settings.neo4j_enabled:
            return None
        if self.neo4j_repository is None:
            self.neo4j_repository = Neo4jRepository.from_settings()
        if not self._neo4j_prepared:
            self.neo4j_repository.prepare()
            self._neo4j_prepared = True
        return self.neo4j_repository

    def _get_embedding_provider(self) -> EmbeddingProvider | None:
        """Build the configured embedding provider lazily at the storage boundary."""
        if self.embedding_provider is None and settings.embedding_enabled:
            self.embedding_provider = build_embedding_provider()
        return self.embedding_provider

    def close(self) -> None:
        """Release the owned Neo4j driver during application shutdown."""
        if not self._executor_closed:
            self._ingestion_executor.shutdown(wait=True, cancel_futures=True)
            self._evaluation_executor.shutdown(wait=True, cancel_futures=True)
            self._executor_closed = True
        self.persist_state()
        if self.neo4j_repository is not None:
            self.neo4j_repository.close()

    def submit_evaluation(self, function, *args: Any) -> None:
        """Schedule a local evaluation job on the owned background executor."""
        self._evaluation_executor.submit(function, *args)

    # ------------------------------------------------------------------
    # Authentication and workspace boundary
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _user_from_data(data: dict[str, Any]) -> UserRecord:
        return UserRecord(
            user_id=str(data["user_id"]),
            email=str(data["email"]),
            display_name=str(data.get("display_name") or data["email"].split("@", 1)[0]),
            password_hash=str(data["password_hash"]),
            role=str(data.get("role") or "member"),
            is_active=bool(data.get("is_active", True)),
            created_at=float(data.get("created_at") or datetime.now().timestamp()),
        )

    @staticmethod
    def _hash_password(password: str) -> str:
        return _PASSWORD_HASHER.hash(password)

    @staticmethod
    def _verify_password(password_hash: str, password: str) -> bool:
        try:
            return _PASSWORD_HASHER.verify(password_hash, password)
        except (InvalidHash, VerificationError, VerifyMismatchError):
            return False

    def _cache_user(self, user: UserRecord) -> UserRecord:
        self.users[user.user_id] = user
        self.users_by_email[user.email] = user.user_id
        return user

    def create_user(self, display_name: str, email: str, password: str) -> UserRecord:
        normalized_email = self._normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("A valid email is required")
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        with self._lock:
            repository = self._get_neo4j_repository()
            if repository is not None and repository.get_user_by_email(normalized_email):
                raise ValueError("An account with this email already exists")
            if repository is None and normalized_email in self.users_by_email:
                raise ValueError("An account with this email already exists")
            user = UserRecord(
                user_id=f"usr_{uuid4().hex[:12]}",
                email=normalized_email,
                display_name=display_name.strip() or normalized_email.split("@", 1)[0],
                password_hash=self._hash_password(password),
            )
            if repository is not None:
                try:
                    repository.create_user(
                        {
                            "user_id": user.user_id,
                            "email": user.email,
                            "display_name": user.display_name,
                            "password_hash": user.password_hash,
                            "role": user.role,
                            "is_active": user.is_active,
                            "created_at": user.created_at,
                            "updated_at": user.created_at,
                            "password_updated_at": user.created_at,
                        }
                    )
                except GraphDBError as exc:
                    if "constraint" in str(exc).lower() or "already exists" in str(exc).lower():
                        raise ValueError("An account with this email already exists") from exc
                    raise
            self._cache_user(user)
            self.create_workspace(
                user.user_id,
                f"{user.display_name}'s knowledge base",
            )
            return user

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        with self._lock:
            repository = self._get_neo4j_repository()
            if repository is None:
                return self.users.get(user_id)
            data = repository.get_user_by_id(user_id)
            return self._cache_user(self._user_from_data(data)) if data else None

    def get_user_by_email(self, email: str) -> UserRecord | None:
        normalized_email = self._normalize_email(email)
        with self._lock:
            repository = self._get_neo4j_repository()
            if repository is None:
                user_id = self.users_by_email.get(normalized_email)
                return self.users.get(user_id or "")
            data = repository.get_user_by_email(normalized_email)
            return self._cache_user(self._user_from_data(data)) if data else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            repository = self._get_neo4j_repository()
            if repository is None:
                return list(self.users.values())
            users = [self._user_from_data(data) for data in repository.list_users()]
            for user in users:
                self._cache_user(user)
            return users

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> UserRecord | None:
        with self._lock:
            repository = self._get_neo4j_repository()
            if repository is None:
                user = self.users.get(user_id)
                if user is None:
                    return None
                if role is not None:
                    user.role = role
                if is_active is not None:
                    user.is_active = is_active
                return user
            data = repository.update_user(
                user_id,
                role=role,
                is_active=is_active,
                updated_at=datetime.now().timestamp(),
            )
            if not data:
                return None
            return self._cache_user(self._user_from_data(data))

    def authenticate(self, email: str, password: str) -> UserRecord:
        normalized_email = self._normalize_email(email)
        with self._lock:
            repository = self._get_neo4j_repository()
            user = self.get_user_by_email(normalized_email)
            if user is None or not user.is_active or not self._verify_password(user.password_hash, password):
                raise ValueError("Invalid email or password")
            if repository is not None and _PASSWORD_HASHER.check_needs_rehash(user.password_hash):
                refreshed_hash = self._hash_password(password)
                data = repository.update_user(
                    user.user_id,
                    password_hash=refreshed_hash,
                    updated_at=datetime.now().timestamp(),
                    password_updated_at=datetime.now().timestamp(),
                )
                if data:
                    user = self._cache_user(self._user_from_data(data))
            return user

    def create_workspace(self, owner_id: str, name: str) -> WorkspaceRecord:
        workspace = WorkspaceRecord(
            workspace_id=f"ws_{uuid4().hex[:12]}",
            name=name.strip() or "Knowledge base",
            description="A-RAG document workspace",
            owner_id=owner_id,
        )
        self.workspaces[workspace.workspace_id] = workspace
        self.workspace_members[workspace.workspace_id] = {owner_id: "owner"}
        return workspace

    def list_workspaces(self, user_id: str, query: str = "") -> list[WorkspaceRecord]:
        query_lower = query.strip().lower()
        allowed = {
            workspace_id
            for workspace_id, members in self.workspace_members.items()
            if user_id in members
        }
        return [
            workspace
            for workspace in self.workspaces.values()
            if workspace.workspace_id in allowed
            and (not query_lower or query_lower in f"{workspace.name} {workspace.workspace_id}".lower())
        ]

    def workspace(self, workspace_id: str, user_id: str | None = None) -> WorkspaceRecord:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise KeyError(f"Workspace not found: {workspace_id}")
        if user_id is not None and self.workspace_members.get(workspace_id, {}).get(user_id) is None:
            raise PermissionError("User is not a member of this workspace")
        return workspace

    # ------------------------------------------------------------------
    # Zone 1 ingestion checkpoint
    # ------------------------------------------------------------------
    def upload_bytes(
        self,
        workspace_id: str,
        filename: str,
        content_type: str,
        source_bytes: bytes,
        *,
        relative_path: str = "",
        replace_document_id: str = "",
    ) -> StoredDocument:
        """Persist source bytes and create an upload-only ingestion job.

        This method deliberately does not parse, chunk, embed, or write Neo4j.
        The caller must explicitly invoke :meth:`start_ingestion` to move the
        document into ``processing``.
        """
        workspace = self.workspace(workspace_id)
        safe_path = _safe_relative_path(relative_path, filename)
        source_path = f"uploads/{safe_path}"
        safe_name = Path(safe_path).name
        digest = hashlib.sha256(source_bytes).hexdigest()[:16]
        document_id = digest
        upload_action = "created"
        if replace_document_id and replace_document_id in self.documents:
            previous = self.documents[replace_document_id]
            if previous.workspace_id == workspace_id and previous.editable:
                document_id = replace_document_id
                upload_action = "updated"

        upload_path = self.root / "workspaces" / workspace.workspace_id / safe_path
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_bytes(source_bytes)

        stored = StoredDocument(
            document_id=document_id,
            workspace_id=workspace_id,
            name=safe_name,
            source_path=source_path,
            content_type=content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            source_bytes=source_bytes,
            upload_action=upload_action,
            storage_path=upload_path.relative_to(self.root).as_posix(),
        )
        job = JobRecord(
            job_id=f"job_{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            document_id=document_id,
        )
        stored.job_id = job.job_id
        with self._lock:
            previous = self.documents.get(document_id)
            if previous and previous.job_id:
                self.jobs.pop(previous.job_id, None)
            self.jobs[job.job_id] = job
            self.documents[document_id] = stored
        self.persist_state()
        logger.info(f"WEB_UPLOAD_ACCEPTED document={document_id} status=uploaded")
        return stored

    def start_ingestion(self, workspace_id: str, document_ids: list[str]) -> dict[str, Any]:
        """Start ingestion for uploaded or failed documents and return job handles.

        The state transition to ``processing`` happens before the job is
        submitted, so the API cannot report an uploaded document as ready while
        its work is still pending. The executor owns only background
        scheduling; all document work is delegated to the single
        ``ingestion_pipeline`` StateGraph.
        """
        self.workspace(workspace_id)
        accepted: list[str] = []
        jobs: list[JobRecord] = []
        skipped = {"unchanged": [], "already_active": []}
        with self._lock:
            for document_id in document_ids:
                stored = self.find_document(document_id, workspace_id)
                if stored.status == "indexed":
                    skipped["unchanged"].append(document_id)
                    continue
                if stored.status == "processing":
                    skipped["already_active"].append(document_id)
                    continue
                if stored.status not in {"uploaded", "failed"}:
                    raise IngestionError(
                        "Document cannot be started from its current status",
                        details={"document_id": document_id, "status": stored.status},
                    )
                job = self.jobs.get(stored.job_id or "")
                if job is None:
                    job = JobRecord(
                        job_id=f"job_{uuid4().hex[:12]}",
                        workspace_id=workspace_id,
                        document_id=document_id,
                    )
                    stored.job_id = job.job_id
                    self.jobs[job.job_id] = job
                now = _utc_now()
                stored.status = "processing"
                stored.error = None
                job.status = "processing"
                job.started_at = now
                job.finished_at = None
                job.error = None
                job.flow_stage = "queued"
                job.flow_events.clear()
                accepted.append(document_id)
                jobs.append(job)

        self.persist_state()

        for job in jobs:
            try:
                self._ingestion_executor.submit(self._run_ingestion_job, job.job_id)
            except Exception as exc:
                self._mark_ingestion_failed(job.job_id, exc)
        return {
            "accepted": accepted,
            "skipped": skipped,
            "jobs": [self._job_response(job) for job in jobs],
        }

    def _run_ingestion_job(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            stored = self.documents.get(job.document_id) if job else None
            if job is None or stored is None:
                return
            workspace_id = stored.workspace_id
            source_path = stored.source_path
            source_file = (
                self.root / stored.storage_path
                if stored.storage_path
                else self.root / "workspaces" / workspace_id / Path(source_path.removeprefix("uploads/"))
            )
            document_id = stored.document_id
        logger.info(f"WEB_INGESTION_STARTED document={document_id} status=processing")
        try:
            repository = self._get_neo4j_repository()
            runtime = IngestionPipelineRuntime(
                file_path=str(source_file),
                workspace_id=workspace_id,
                source_path=source_path,
                repository=repository,
                embedding_provider=self._get_embedding_provider(),
            )
            initial_state = initial_ingestion_state(
                runtime,
                job_id=job_id,
                document_id=document_id,
            )
            task_started_at: dict[str, float] = {}
            final_state = execute_ingestion_pipeline(
                initial_state,
                on_event=lambda part: self._record_pipeline_event(
                    job_id,
                    part,
                    task_started_at,
                ),
            )
            parsed = final_state.get("document")
            chunks = list(final_state.get("chunks", []))
            validation = final_state.get("validation", {})
            if not isinstance(parsed, ParsedDocument):
                raise IngestionError(
                    "Ingestion graph completed without a ParsedDocument",
                    details={"pipeline_stage": final_state.get("pipeline_stage")},
                )
            if validation.get("status") != "valid":
                raise IngestionError(
                    "Ingestion provenance validation failed",
                    details=validation,
                )
            parsed.profiler_result = final_state.get("profiler_result")
            parsed.doc_metadata.update(
                {
                    "web_checkpoint": "zone_2" if repository is not None else "zone_1",
                    "source_path": source_path,
                    "parser_engine": (
                        final_state.get("profiler_result").recommended_engine
                        if final_state.get("profiler_result") is not None
                        else None
                    ),
                    "pipeline_entities": len(final_state.get("entities", [])),
                }
            )
            with self._lock:
                stored = self.documents[document_id]
                job = self.jobs[job_id]
                stored.parsed_document = parsed
                stored.chunks = chunks
                stored.status = "indexed"
                job.status = "indexed"
                job.finished_at = _utc_now()
            self.persist_state()
            logger.info(
                f"WEB_INGESTION_COMPLETED document={document_id} "
                f"chunks={len(chunks)} status=indexed"
            )
        except Exception as exc:
            self._mark_ingestion_failed(job_id, exc)
            logger.exception(f"WEB_INGESTION_FAILED document={document_id}: {exc}")

    def _record_pipeline_event(
        self,
        job_id: str,
        part: dict[str, Any],
        task_started_at: dict[str, float],
    ) -> None:
        """Persist a safe projection of one real LangGraph v2 stream part."""

        event_type = str(part.get("type", ""))
        namespace = [str(item) for item in part.get("ns", ())]
        data = part.get("data")
        event: dict[str, Any] | None = None
        now = time.perf_counter()
        if event_type == "tasks" and isinstance(data, dict):
            task_id = str(data.get("id", ""))
            node = str(data.get("name", "unknown"))
            if "input" in data and "result" not in data and "error" not in data:
                task_started_at[task_id] = now
                event = {
                    "event": "node_started",
                    "node": node,
                    "namespace": namespace,
                    "status": "running",
                }
            elif "result" in data or "error" in data:
                started = task_started_at.pop(task_id, now)
                event = {
                    "event": "node_completed",
                    "node": node,
                    "namespace": namespace,
                    "status": "failed" if data.get("error") else "completed",
                    "duration_ms": round((now - started) * 1000, 2),
                    "updated_keys": sorted(data.get("result", {}).keys())
                    if isinstance(data.get("result"), dict)
                    else [],
                }
        elif event_type == "custom" and isinstance(data, dict):
            event = {
                "event": "progress",
                "namespace": namespace,
                "kind": str(data.get("kind", "custom")),
                "payload": {key: value for key, value in data.items() if key != "kind"},
            }
        elif event_type == "updates" and isinstance(data, dict):
            event = {
                "event": "state_update",
                "namespace": namespace,
                "nodes": sorted(str(key) for key in data),
            }
        if event is None:
            return
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            event["sequence"] = len(job.flow_events) + 1
            event["timestamp"] = _utc_now()
            job.flow_events.append(event)
            if len(job.flow_events) > 2000:
                del job.flow_events[:-2000]
            if event["event"] == "node_started" or (
                event["event"] == "node_completed" and event.get("status") == "failed"
            ):
                job.flow_stage = str(event["node"])
            should_checkpoint = len(job.flow_events) % 10 == 0 or (
                event["event"] == "node_completed" and event.get("status") == "failed"
            )
        if should_checkpoint:
            self.persist_state()

    def _mark_ingestion_failed(self, job_id: str, exc: Exception) -> None:
        error = self._error_payload(exc)
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            stored = self.documents.get(job.document_id)
            if stored is not None:
                stored.status = "failed"
                stored.error = error
            job.status = "failed"
            job.finished_at = _utc_now()
            job.error = error
        self.persist_state()

    @staticmethod
    def _job_response(job: JobRecord) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "project_id": job.workspace_id,
            "document_id": job.document_id,
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "flow_stage": job.flow_stage,
            "flow_event_count": len(job.flow_events),
        }

    @staticmethod
    def _error_payload(exc: Exception) -> dict[str, Any]:
        if hasattr(exc, "to_dict"):
            return exc.to_dict()  # type: ignore[no-any-return]
        return {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "details": {},
        }

    # ------------------------------------------------------------------
    # Serialization contracts consumed by web/src/types.ts
    # ------------------------------------------------------------------
    def document_record(self, stored: StoredDocument) -> dict[str, Any]:
        pages = stored.parsed_document.total_pages if stored.parsed_document else 1
        return {
            "id": stored.document_id,
            "name": stored.name,
            "workspace": stored.workspace_id,
            "page": str(pages),
            "status": stored.status,
            "job_id": stored.job_id,
            "uploaded_at": stored.uploaded_at,
            "source_path": stored.source_path,
            "size_bytes": len(stored.source_bytes),
            "content_type": stored.content_type,
            "editable": stored.editable,
            "upload_action": stored.upload_action,
            "content_changed": True,
        }

    def list_documents(
        self,
        workspace_id: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query_lower = query.strip().lower()
        documents = [
            document
            for document in self.documents.values()
            if document.workspace_id == workspace_id
            and (
                not query_lower
                or query_lower in f"{document.name} {document.source_path}".lower()
            )
        ]
        documents.sort(key=lambda item: item.uploaded_at, reverse=True)
        return [self.document_record(item) for item in documents[offset : offset + limit]]

    def find_document(self, document_id: str, workspace_id: str | None = None) -> StoredDocument:
        document = self.documents.get(document_id)
        if document is None or (workspace_id and document.workspace_id != workspace_id):
            raise KeyError(f"Document not found: {document_id}")
        return document

    def ingestion_status(self, workspace_id: str, document_id: str = "", job_id: str = "") -> dict[str, Any]:
        stored: StoredDocument | None = None
        job: JobRecord | None = self.jobs.get(job_id) if job_id else None
        if document_id:
            stored = self.find_document(document_id, workspace_id)
            job = self.jobs.get(stored.job_id or "")
        elif job is not None and job.workspace_id == workspace_id:
            stored = self.documents.get(job.document_id)
        if stored is None:
            raise KeyError("Ingestion target not found")
        status = stored.status
        readiness = "ready" if status == "indexed" else "failed" if status == "failed" else "not_ready"
        stage = status if status in {"uploaded", "processing", "indexed", "failed"} else None
        return {
            "project_id": workspace_id,
            "target_type": "document" if document_id else "job",
            "target_id": stored.document_id if document_id else (job.job_id if job else stored.document_id),
            "readiness": readiness,
            "ready_for_qa": readiness == "ready",
            "reason_code": "ready" if readiness == "ready" else "ingestion_failed" if readiness == "failed" else "processing",
            "status": status,
            "stage": stage,
            "flow_stage": job.flow_stage if job else None,
            "flow_event_count": len(job.flow_events) if job else 0,
            "job_id": stored.job_id,
            "chunk_count": len(stored.chunks),
            "retryable": readiness == "failed",
            "error": stored.error,
            "updated_at": (
                job.finished_at
                if job and job.finished_at
                else job.started_at
                if job and job.started_at
                else stored.uploaded_at
            ),
        }

    def ingestion_events(
        self,
        workspace_id: str,
        *,
        job_id: str,
        after: int = 0,
    ) -> dict[str, Any]:
        """Return ordered LangGraph execution events after a sequence number."""

        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job.workspace_id != workspace_id:
                raise KeyError("Ingestion job not found")
            events = [event for event in job.flow_events if int(event.get("sequence", 0)) > after]
            return {
                "job_id": job.job_id,
                "document_id": job.document_id,
                "status": job.status,
                "flow_stage": job.flow_stage,
                "events": events,
                "next_after": int(events[-1]["sequence"]) if events else after,
                "completed": job.status in {"indexed", "failed"},
            }

    def overview(self, workspace_id: str) -> dict[str, Any]:
        records = [doc for doc in self.documents.values() if doc.workspace_id == workspace_id]
        return {
            "workspace_id": workspace_id,
            "document_count": len(records),
            "indexed_count": sum(doc.status == "indexed" for doc in records),
            "processing_count": sum(doc.status == "processing" for doc in records),
            "uploaded_count": sum(doc.status == "uploaded" for doc in records),
            "failed_count": sum(doc.status == "failed" for doc in records),
            "storage_bytes": sum(len(doc.source_bytes) for doc in records),
            "recent_documents": [self.document_record(doc) for doc in sorted(records, key=lambda item: item.uploaded_at, reverse=True)[:5]],
        }

    def preview(self, document: StoredDocument) -> dict[str, Any]:
        return {
            "document_id": document.document_id,
            "name": document.name,
            "source_path": document.source_path,
            "status": document.status,
            "content_type": document.content_type,
            "size_bytes": len(document.source_bytes),
            "preview": _short_preview(document.parsed_document) if document.parsed_document else "",
            "preview_available": document.parsed_document is not None,
        }

    def review(self, document: StoredDocument) -> dict[str, Any]:
        """Return the safe, layout-aware projection used by the ingest review UI.

        Source bytes are deliberately kept behind the authenticated content
        endpoint.  This response only exposes parser output and normalized
        coordinates; internal artifact paths are not sent to the browser.
        """
        parsed = document.parsed_document
        if parsed is None:
            return {
                **self.preview(document),
                "total_pages": 0,
                "elements": [],
                "pages": [],
                "markdown": "",
                "stats": {},
                "has_bounding_boxes": False,
                "review_available": False,
            }

        elements: list[dict[str, Any]] = []
        pages: dict[int, list[dict[str, Any]]] = {}
        for element in parsed.elements:
            metadata = element.metadata
            bounding_box = metadata.bounding_box.model_dump(mode="json") if metadata.bounding_box else None
            item = {
                "element_id": element.element_id,
                "content": element.content,
                "raw_content": element.raw_content,
                "page_number": metadata.page_number,
                "element_index": metadata.element_index,
                "element_type": metadata.element_type,
                "bounding_box": bounding_box,
                "confidence": metadata.confidence,
                "parent_header": metadata.parent_header,
                "header_level": metadata.header_level,
                "section_path": metadata.section_path,
                "extra": metadata.extra,
                "vlm_caption": element.vlm_caption,
            }
            elements.append(item)
            pages.setdefault(metadata.page_number, []).append(item)

        return {
            **self.preview(document),
            "total_pages": parsed.total_pages,
            "elements": elements,
            "pages": [
                {"page_number": page_number, "elements": page_elements}
                for page_number, page_elements in sorted(pages.items())
            ],
            "markdown": _short_preview(parsed, limit=120000),
            "stats": parsed.stats,
            "has_bounding_boxes": any(item["bounding_box"] is not None for item in elements),
            "review_available": True,
        }

    def metadata(self, document: StoredDocument) -> dict[str, Any]:
        parsed = document.parsed_document
        profiler = parsed.profiler_result.model_dump(mode="json") if parsed and parsed.profiler_result else None
        return {
            "project_id": document.workspace_id,
            "document_id": document.document_id,
            "name": document.name,
            "source": "upload",
            "source_path": document.source_path,
            "status": document.status,
            "content_type": document.content_type,
            "size_bytes": len(document.source_bytes),
            "version": 1,
            "hash": hashlib.sha256(document.source_bytes).hexdigest(),
            "document_type": parsed.file_type if parsed else None,
            "development_stage": "zone_2" if settings.neo4j_enabled or self.neo4j_repository else "zone_1",
            "storage_backend": "neo4j+local_json" if settings.neo4j_enabled or self.neo4j_repository else "local_json",
            "chunk_count": len(document.chunks),
            "updated_at": document.uploaded_at,
            "metadata": {
                "parser": parsed.doc_metadata if parsed else {},
                "profiler_result": profiler,
                "lineage": {
                    "element_chunk_map": self._element_chunk_map(document),
                    "section_chunk_map": self._section_chunk_map(document),
                },
            },
        }

    @staticmethod
    def _element_chunk_map(document: StoredDocument) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for chunk in document.chunks:
            for element_id in chunk.metadata.element_ids:
                result.setdefault(element_id, []).append(chunk.chunk_id)
        return result

    @staticmethod
    def _section_chunk_map(document: StoredDocument) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for chunk in document.chunks:
            key = " > ".join(chunk.metadata.section_path)
            if key:
                result.setdefault(key, []).append(chunk.chunk_id)
        return result

    # ------------------------------------------------------------------
    # Query boundary: Main + specialist graph in both modes; choose the
    # workspace-scoped Neo4j or local lexical adapter underneath.
    # ------------------------------------------------------------------
    def query(
        self,
        workspace_id: str,
        question: str,
        *,
        file_paths: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        chat_session_id: str | None = None,
        save_history: bool = True,
    ) -> dict[str, Any]:
        repository = self._get_neo4j_repository()
        session = self.chat_sessions.get(chat_session_id or "", {})
        saved_history = [
            {"role": message["role"], "content": message["content"]}
            for turn in session.get("turns", [])[-6:]
            for message in (
                {"role": "user", "content": turn.get("question", "")},
                {"role": "assistant", "content": turn.get("answer", "")},
            )
            if message["content"]
        ]
        history = (conversation_history or saved_history)[-12:]
        return self._query_agentic(
            workspace_id,
            question,
            file_paths=file_paths,
            history=history,
            chat_session_id=chat_session_id,
            save_history=save_history,
            repository=repository,
        )

    def _local_search_tool(
        self,
        *,
        query: str,
        limit: int,
        filter_metadata: dict[str, Any] | None,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        """Workspace-scoped lexical adapter used by ToolNode without Neo4j."""
        query_words = [word for word in re.findall(r"\w+", query.lower()) if len(word) > 2]
        allowed_paths = set((filter_metadata or {}).get("file_paths", []))
        candidates: list[tuple[float, ContentChunk, StoredDocument]] = []
        with self._lock:
            documents = [
                document for document in self.documents.values()
                if document.workspace_id == workspace_id
                and (not allowed_paths or document.source_path in allowed_paths)
            ]
        for document in documents:
            for chunk in document.chunks:
                content_lower = chunk.content.lower()
                matches = sum(word in content_lower for word in query_words)
                score = matches / max(len(query_words), 1)
                if score > 0 or not query_words:
                    candidates.append((score, chunk, document))
        candidates.sort(key=lambda item: (-item[0], item[1].chunk_id))
        results = []
        for score, chunk, document in candidates[: max(1, min(limit, settings.retrieval_top_k))]:
            metadata = chunk.metadata.model_dump(mode="json")
            metadata.update({"workspace_id": workspace_id, "source_path": document.source_path})
            results.append({
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "modality": chunk.metadata.modality,
                "source_type": "local_lexical",
                "source_doc": document.name,
                "score": score,
                "metadata": metadata,
                "vlm_caption": chunk.vlm_caption,
                "document_id": document.document_id,
                "page_numbers": chunk.metadata.page_numbers,
                "element_ids": chunk.metadata.element_ids,
                "section_path": chunk.metadata.section_path,
                "parent_chunk_id": chunk.metadata.parent_chunk_id,
            })
        return results

    def _local_graph_tool(
        self,
        *,
        entity_query: str,
        max_hops: int,
        workspace_id: str,
        file_paths: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Local Zone 1 has no indexed relation graph; return no fabricated edges."""
        return []

    def _local_evidence_tool(self, *, workspace_id: str, chunk_id: str) -> dict[str, Any] | None:
        with self._lock:
            for document in self.documents.values():
                if document.workspace_id != workspace_id:
                    continue
                for chunk in document.chunks:
                    if chunk.chunk_id == chunk_id:
                        result = chunk.model_dump(mode="json")
                        result["source_doc"] = document.name
                        result["source_path"] = document.source_path
                        result["metadata"]["source_path"] = document.source_path
                        return result
        return None

    def _query_agentic(
        self,
        workspace_id: str,
        question: str,
        *,
        file_paths: list[str] | None,
        history: list[dict[str, str]] | None = None,
        chat_session_id: str | None,
        save_history: bool,
        repository: Neo4jRepository | None,
    ) -> dict[str, Any]:
        """Execute the Zone 2 graph against Neo4j or the local development adapter."""
        from src.agents.orchestrator.graph import agentic_rag_app
        from src.agents.orchestrator.state import create_initial_agent_state

        self._configure_agentic_retrieval(repository)
        final_state = agentic_rag_app.invoke(
            create_initial_agent_state(
                query=question,
                history=history or [],
                workspace_id=workspace_id,
                file_paths=file_paths or [],
                max_retries=settings.max_reflection_retries,
                max_agent_handoffs=settings.max_agent_handoffs,
                max_tool_rounds=settings.max_tool_rounds_per_agent,
            )
        )
        return self.agentic_response_from_state(
            workspace_id,
            question,
            final_state,
            chat_session_id=chat_session_id,
            save_history=save_history,
        )

    def configure_agentic_retrieval(self) -> None:
        """Prepare scoped tools before the streaming router invokes the graph."""
        self._configure_agentic_retrieval(self._get_neo4j_repository())

    def _configure_agentic_retrieval(self, repository: Neo4jRepository | None) -> None:
        from src.mcp_server.tools.kb_retrieval_tools import configure_retrieval

        configure_retrieval(
            repository=repository,
            embedding_provider=self._get_embedding_provider() if repository is not None else None,
            local_search=self._local_search_tool if repository is None else None,
            local_graph=self._local_graph_tool if repository is None else None,
            local_evidence=self._local_evidence_tool if repository is None else None,
        )

    def agentic_response_from_state(
        self,
        workspace_id: str,
        question: str,
        final_state: dict[str, Any],
        *,
        chat_session_id: str | None,
        save_history: bool = True,
    ) -> dict[str, Any]:
        """Project one completed AgentState into the public query response.

        This is shared by synchronous ``/v1/query`` and the SSE completion
        callback. Keeping the projection in one boundary guarantees identical
        citation, confidence, answer-id, and chat-history semantics.
        """
        citations = []
        for chunk in final_state.get("retrieved_docs", []):
            metadata = chunk.metadata or {}
            source_path = metadata.get("source_path") or chunk.source_doc
            citations.append(
                {
                    "reference_id": chunk.chunk_id,
                    "document_id": chunk.document_id or metadata.get("document_id"),
                    "file_path": source_path,
                    "source_path": source_path,
                    "content": chunk.content,
                    "snippet": chunk.content[:500],
                    "confidence": chunk.score,
                    "confidence_score": chunk.score,
                    "confidence_label": "high" if chunk.score >= 0.75 else "medium" if chunk.score > 0 else "low",
                    "score_status": "measured",
                    "metadata": metadata,
                }
            )
        critique = final_state.get("critique")
        confidence_score = critique.faithfulness_score if critique else (citations[0]["confidence_score"] if citations else 0.0)
        answer = final_state.get("synthesized_response") or "Chưa có context đã index phù hợp để trả lời câu hỏi này."
        answer_id = f"ans_{uuid4().hex[:12]}"
        session = self._save_chat(
            workspace_id,
            question,
            answer,
            citations,
            chat_session_id=chat_session_id,
            answer_id=answer_id,
        ) if save_history else None
        return {
            "answer_id": answer_id,
            "answer": answer,
            "citations": citations,
            "confidence": {
                "score": confidence_score,
                "label": "high" if confidence_score >= 0.85 else "medium" if confidence_score > 0 else "none",
                "source_count": len(citations),
                "evidence_count": len(final_state.get("retrieved_docs", [])),
                "rationale": "Zone 2 LangGraph over Neo4j vector/full-text/graph retrieval.",
            },
            "chat_session": session,
            "retrieval_trace": final_state.get("retrieval_trace", {}),
            "provenance_validation": final_state.get("provenance_validation", {}),
            "graph_context": final_state.get("graph_context", []),
            "attempt_history": final_state.get("attempt_history", []),
            "agent_handoffs": final_state.get("agent_handoffs", 0),
            "run_status": final_state.get("run_status", "completed"),
        }

    def _save_chat(
        self,
        workspace_id: str,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        *,
        chat_session_id: str | None,
        answer_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = chat_session_id or f"chat_{uuid4().hex[:12]}"
        now = _utc_now()
        session = self.chat_sessions.setdefault(
            session_id,
            {
                "id": session_id,
                "workspace_id": workspace_id,
                "title": question[:80],
                "created_at": now,
                "updated_at": now,
                "turns": [],
            },
        )
        session["updated_at"] = now
        session["turns"].append(
            {
                "id": f"turn_{uuid4().hex[:12]}",
                "answer_id": answer_id,
                "question": question,
                "answer": answer,
                "citations": citations,
                "attachment_paths": [],
                "created_at": now,
            }
        )
        self.persist_state()
        return self.chat_summary(session)

    @staticmethod
    def chat_summary(session: dict[str, Any]) -> dict[str, Any]:
        turns = session.get("turns", [])
        last = turns[-1] if turns else {}
        return {
            "id": session["id"],
            "workspace_id": session["workspace_id"],
            "title": session["title"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "turn_count": len(turns),
            "last_question": last.get("question", ""),
            "last_answer_preview": last.get("answer", "")[:180],
            "last_answer_id": last.get("answer_id"),
            "attachment_paths": last.get("attachment_paths", []),
        }

    def workspace_graph(self, workspace_id: str) -> dict[str, Any]:
        repository = self._get_neo4j_repository()
        if repository is not None:
            return repository.workspace_graph(workspace_id=workspace_id)
        records = [doc for doc in self.documents.values() if doc.workspace_id == workspace_id]
        nodes = [
            {
                "id": doc.document_id,
                "degree": len(doc.chunks),
                "attributes": {"name": doc.name, "source_path": doc.source_path, "status": doc.status},
            }
            for doc in records
        ]
        return {
            "workspace_id": workspace_id,
            "storage": "zone_1_lineage",
            "format": "json",
            "graphml_path": "",
            "node_count": len(nodes),
            "edge_count": 0,
            "is_directed": True,
            "is_multigraph": False,
            "density": 0.0,
            "connected_components": len(nodes),
            "limits": {"nodes": 120, "edges": 240, "include_attributes": True},
            "top_degree_nodes": [{"id": node["id"], "degree": node["degree"]} for node in nodes],
            "nodes": nodes,
            "edges": [],
        }


__all__ = [
    "JobRecord",
    "StoredDocument",
    "UserRecord",
    "WebApplicationService",
    "WorkspaceRecord",
]
