"""Web-facing application service for the current A-RAG checkpoints.

The React application owns presentation only.  This module is the server-side
adapter between its ``/v1`` contract and the current Zone 1 implementation:

    upload bytes -> profiler -> MinerU/native parser -> enrichment
                 -> chunking StateGraph -> provenance gate -> store

The web session/auth shell remains process-local for the UI milestone, while
the document indexing boundary can be switched to the configured Neo4j
repository. Source bytes are written below ``.runtime/web`` for preview and
download. Upload and ingestion are separate commands: upload persists the
original bytes as ``uploaded``; an explicit start command runs the parser,
provenance-safe chunking, embeddings, and Neo4j indexing. No LLM is allowed
to rewrite source text in this path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import mimetypes
from pathlib import Path
import re
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Any
from uuid import uuid4

from src.core.config import logger, settings
from src.core.exceptions import IngestionError
from src.ingestion.chunking import run_chunking
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.mineru_adapter import MinerUAdapter
from src.ingestion.parser.models import ContentChunk, ParsedDocument, ProfilerResult
from src.ingestion.parser.profiler import DocumentProfiler
from src.ingestion.parser.skills import _apply_skills_impl
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


@dataclass
class UserRecord:
    user_id: str
    email: str
    display_name: str
    password_hash: str
    role: str = "member"
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def as_response(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
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
        self.neo4j_repository = neo4j_repository
        self.embedding_provider = embedding_provider
        self._neo4j_prepared = False
        self._ingestion_executor = ThreadPoolExecutor(
            max_workers=settings.ingestion_worker_count,
            thread_name_prefix="ingestion-worker",
        )
        self._executor_closed = False

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
            self._executor_closed = True
        if self.neo4j_repository is not None:
            self.neo4j_repository.close()

    # ------------------------------------------------------------------
    # Authentication and workspace boundary
    # ------------------------------------------------------------------
    def create_user(self, display_name: str, email: str, password: str) -> UserRecord:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("A valid email is required")
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        with self._lock:
            if normalized_email in self.users_by_email:
                raise ValueError("An account with this email already exists")
            user = UserRecord(
                user_id=f"usr_{uuid4().hex[:12]}",
                email=normalized_email,
                display_name=display_name.strip() or normalized_email.split("@", 1)[0],
                password_hash=hashlib.sha256(password.encode("utf-8")).hexdigest(),
            )
            self.users[user.user_id] = user
            self.users_by_email[normalized_email] = user.user_id
            self.create_workspace(
                user.user_id,
                f"{user.display_name}'s knowledge base",
            )
            return user

    def authenticate(self, email: str, password: str) -> UserRecord:
        normalized_email = email.strip().lower()
        with self._lock:
            user_id = self.users_by_email.get(normalized_email)
            user = self.users.get(user_id or "")
            password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
            if user is None or user.password_hash != password_hash:
                raise ValueError("Invalid email or password")
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
        logger.info(f"WEB_UPLOAD_ACCEPTED document={document_id} status=uploaded")
        return stored

    def start_ingestion(self, workspace_id: str, document_ids: list[str]) -> dict[str, Any]:
        """Start ingestion for uploaded or failed documents and return job handles.

        The state transition to ``processing`` happens before the job is
        submitted, so the API cannot report an uploaded document as ready while
        its work is still pending. The executor owns only orchestration timing;
        parsing, chunking, provenance validation, embedding, and persistence
        remain in :meth:`_run_ingestion_job`.
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
                accepted.append(document_id)
                jobs.append(job)

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
            source_file = self.root / "workspaces" / workspace_id / Path(source_path.removeprefix("uploads/"))
            document_id = stored.document_id
        logger.info(f"WEB_INGESTION_STARTED document={document_id} status=processing")
        try:
            profiler = DocumentProfiler().profile(str(source_file))
            parsed = self._parse_with_profile(str(source_file), profiler)
            parsed.profiler_result = profiler
            parsed.doc_metadata.update(
                {
                    "web_checkpoint": "zone_2" if settings.neo4j_enabled or self.neo4j_repository else "zone_1",
                    "source_path": source_path,
                    "parser_engine": profiler.recommended_engine,
                }
            )
            enriched = _apply_skills_impl(parsed)
            chunk_state = run_chunking(
                enriched,
                max_tokens=settings.chunk_max_tokens,
                overlap_tokens=settings.chunk_overlap_tokens,
            )
            if chunk_state.get("stage") != "validated":
                raise IngestionError(
                    "Chunking provenance validation failed",
                    details={"errors": chunk_state.get("errors", [])},
                )
            chunks = list(chunk_state.get("chunks", []))
            repository = self._get_neo4j_repository()
            if repository is not None:
                repository.upsert_document(
                    enriched,
                    workspace_id=workspace_id,
                    source_path=source_path,
                    chunks=chunks,
                    embedding_provider=self._get_embedding_provider(),
                )
            with self._lock:
                stored = self.documents[document_id]
                job = self.jobs[job_id]
                stored.parsed_document = enriched
                stored.chunks = chunks
                stored.status = "indexed"
                job.status = "indexed"
                job.finished_at = _utc_now()
            logger.info(
                f"WEB_INGESTION_COMPLETED document={document_id} "
                f"engine={profiler.recommended_engine} chunks={len(chunks)} status=indexed"
            )
        except Exception as exc:
            self._mark_ingestion_failed(job_id, exc)
            logger.exception(f"WEB_INGESTION_FAILED document={document_id}: {exc}")

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

    @staticmethod
    def _job_response(job: JobRecord) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "project_id": job.workspace_id,
            "document_id": job.document_id,
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    @staticmethod
    def _parse_with_profile(file_path: str, profiler: ProfilerResult) -> ParsedDocument:
        if profiler.recommended_engine == "mineru":
            return MinerUAdapter().parse_file(file_path, backend=settings.mineru_backend)
        if profiler.recommended_engine == "native":
            return DocumentLayoutParser().parse_file(file_path)
        raise IngestionError(
            f"No Zone 1 parser is configured for '.{profiler.file_type}'",
            details={"file_type": profiler.file_type, "policy": "unsupported_format"},
        )

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
            "storage_backend": "neo4j" if settings.neo4j_enabled or self.neo4j_repository else "local_memory",
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
    # Query boundary: use the live Zone 2 graph when enabled; retain the local
    # matcher only as an explicit development fallback while Neo4j is disabled.
    # ------------------------------------------------------------------
    def query(
        self,
        workspace_id: str,
        question: str,
        *,
        file_paths: list[str] | None = None,
        chat_session_id: str | None = None,
        save_history: bool = True,
    ) -> dict[str, Any]:
        repository = self._get_neo4j_repository()
        if repository is not None:
            return self._query_agentic(
                workspace_id,
                question,
                file_paths=file_paths,
                chat_session_id=chat_session_id,
                save_history=save_history,
                repository=repository,
            )

        query_lower = question.lower()
        words = [word for word in re.findall(r"\w+", query_lower) if len(word) > 2]
        allowed_paths = set(file_paths or [])
        candidates: list[tuple[float, ContentChunk, str]] = []
        for document in self.documents.values():
            if document.workspace_id != workspace_id:
                continue
            if allowed_paths and document.source_path not in allowed_paths:
                continue
            for chunk in document.chunks:
                matches = sum(word in chunk.content.lower() for word in words)
                score = matches / max(len(words), 1)
                if score > 0 or not words:
                    candidates.append((score, chunk, document.source_path))
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = [chunk for _, chunk, _ in candidates[: settings.retrieval_top_k]]
        citations = [
            {
                "reference_id": chunk.chunk_id,
                "document_id": chunk.metadata.document_id,
                "file_path": source_path,
                "source_path": source_path,
                "content": chunk.content,
                "snippet": chunk.content[:500],
                "confidence": score,
                "confidence_score": score,
                "confidence_label": "high" if score >= 0.75 else "medium" if score > 0 else "low",
                "score_status": "measured",
            }
            for score, chunk, source_path in candidates[: settings.retrieval_top_k]
        ]
        if selected:
            answer = "\n\n".join(
                f"{chunk.content} [Chunk: {chunk.chunk_id}]" for chunk in selected[:3]
            )
            confidence_score = citations[0]["confidence_score"] if citations else 0.0
        else:
            answer = "Chưa có context đã index phù hợp để trả lời câu hỏi này."
            confidence_score = 0.0
        answer_id = f"ans_{uuid4().hex[:12]}"
        session = self._save_chat(
            workspace_id,
            question,
            answer,
            citations,
            chat_session_id=chat_session_id,
        ) if save_history else None
        return {
            "answer_id": answer_id,
            "answer": answer,
            "citations": citations,
            "confidence": {
                "score": confidence_score,
                "label": "high" if confidence_score >= 0.75 else "medium" if confidence_score > 0 else "none",
                "source_count": len(citations),
                "evidence_count": len(selected),
                "rationale": "Deterministic Zone 1 chunk match; live vector/BM25 retrieval is a Zone 2 checkpoint.",
            },
            "chat_session": session,
        }

    def _query_agentic(
        self,
        workspace_id: str,
        question: str,
        *,
        file_paths: list[str] | None,
        chat_session_id: str | None,
        save_history: bool,
        repository: Neo4jRepository,
    ) -> dict[str, Any]:
        """Execute the Zone 2 graph against the same Neo4j repository used for writes."""
        from src.agents.orchestrator.graph import agentic_rag_app
        from src.agents.orchestrator.state import create_initial_agent_state
        from src.mcp_server.tools.kb_retrieval_tools import configure_retrieval

        configure_retrieval(
            repository=repository,
            embedding_provider=self._get_embedding_provider(),
        )
        final_state = agentic_rag_app.invoke(
            create_initial_agent_state(
                query=question,
                history=[],
                workspace_id=workspace_id,
                file_paths=file_paths or [],
                max_retries=settings.max_reflection_retries,
            )
        )
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
        }

    def _save_chat(
        self,
        workspace_id: str,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        *,
        chat_session_id: str | None,
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
                "answer_id": None,
                "question": question,
                "answer": answer,
                "citations": citations,
                "attachment_paths": [],
                "created_at": now,
            }
        )
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
