"""FastAPI compatibility boundary for the imported A-RAG web application.

This module is deliberately thin.  The API layer owns HTTP/session concerns;
``WebApplicationService`` owns the Zone 1 workflow and its contracts:

    HTTP upload -> profiler -> MinerU/native parser -> skills -> chunk graph
                 -> provenance validator -> document/status/metadata response

Authentication users and document indexing can be switched to the configured
Neo4j repository. Source bytes and parsed artifacts remain real at every
checkpoint, while background workers are separate concerns.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import math
import posixpath
from pathlib import Path
from typing import Any
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from src.api.service import StoredDocument, WebApplicationService
from src.api.router import router as agent_stream_router
from src.core.config import settings


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Release long-lived storage clients when the HTTP process exits."""
    yield
    service.close()


app = FastAPI(
    title="A-RAG Web API",
    version="0.1.0",
    description="Web adapter for the Agentic RAG ingestion and Neo4j retrieval checkpoints.",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_origin_regex=settings.cors_allow_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.include_router(agent_stream_router)

service = WebApplicationService()
_mcp_credentials: dict[str, dict[str, Any]] = {}
_evaluation_jobs = service.evaluation_jobs


@app.middleware("http")
async def checkpoint_local_service_state(request: Request, call_next):
    """Checkpoint metadata after API mutations; file/Neo4j writes stay at their boundary."""
    try:
        return await call_next(request)
    finally:
        service.persist_state()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=96)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    accept_terms: bool


class SigninRequest(BaseModel):
    email: str
    password: str
    remember: bool = False


class PasswordResetRequest(BaseModel):
    email: str


class PasswordChangeRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=12, max_length=128)


class LanguageRequest(BaseModel):
    locale: str = Field(min_length=2, max_length=8)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class WorkspaceDeleteRequest(BaseModel):
    confirmation: str


class RenameDocumentRequest(BaseModel):
    workspace_id: str
    name: str = Field(min_length=1, max_length=255)


class SyncDocumentsRequest(BaseModel):
    workspace_id: str
    document_ids: list[str] = Field(default_factory=list)


class MoveDocumentsRequest(BaseModel):
    workspace_id: str
    destination_workspace_id: str
    document_ids: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    workspace_id: str
    question: str = Field(min_length=1, max_length=16000)
    file_paths: list[str] = Field(default_factory=list)
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    chat_session_id: str | None = None
    save_history: bool = True
    locale: str = "vi"


class AssistantToolRequest(BaseModel):
    force_tool: str
    message: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    chat_session_id: str | None = None
    save_history: bool = True


class AnswerFeedbackRequest(BaseModel):
    project_id: str
    answer_id: str
    rating: str
    idempotency_key: str | None = None


class AdminUserUpdateRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class AdminFeedbackUpdateRequest(BaseModel):
    status: str
    admin_note: str | None = None
    expected_version: int = 1


class WorkspaceRoleRequest(BaseModel):
    role: str


class McpCredentialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ttl_days: int = Field(default=30, ge=1, le=365)
    workspace_id: str


class AdminMcpCredentialRequest(McpCredentialRequest):
    user_id: str


class EvaluationScoreRequest(BaseModel):
    display_metrics: list[str] = Field(default_factory=list)
    row_ids: list[str] = Field(default_factory=list)
    failed_only: bool = False


class EvaluationMetricsRequest(BaseModel):
    display_metrics: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Boundary helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = service.get_user_by_id(str(user_id))
    return user if user and user.is_active else None


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = uuid4().hex
        request.session["csrf_token"] = token
    return str(token)


def _require_user(request: Request):
    user = _session_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def _require_workspace(request: Request, workspace_id: str):
    user = _require_user(request)
    try:
        return service.workspace(workspace_id, user.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _handle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if hasattr(exc, "to_dict"):
        return HTTPException(status_code=502, detail=exc.to_dict())
    return HTTPException(status_code=500, detail={"message": str(exc), "error_type": type(exc).__name__})


def _workspace_response(workspace_id: str) -> dict[str, Any]:
    workspace = service.workspace(workspace_id)
    records = service.list_documents(workspace_id)
    role = next(iter(service.workspace_members.get(workspace_id, {}).values()), "viewer")
    return {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "description": workspace.description,
        "status": "ready",
        "document_count": len(records),
        "access_role": role,
    }


def _auth_response(request: Request, user) -> dict[str, Any]:
    request.session["user_id"] = user.user_id
    token = _csrf_token(request)
    return {"authenticated": True, "user": user.as_response(), "csrf_token": token}


def _admin_required(request: Request):
    user = _require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


def _document_ids_for_workspace(workspace_id: str, ids: list[str]) -> list[StoredDocument]:
    documents: list[StoredDocument] = []
    for document_id in ids:
        document = service.find_document(document_id, workspace_id)
        documents.append(document)
    return documents


# ---------------------------------------------------------------------------
# Health and shell bootstrap
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "a-rag-web-api",
        "zone": "zone_2",
        "retrieval_backend": "neo4j" if settings.neo4j_enabled else "local_lexical",
        "mineru_base_url": settings.mineru_base_url,
        "storage_backend": "neo4j+local_json" if settings.neo4j_enabled else "local_json",
    }


@app.get("/v1/ui/bootstrap")
def ui_bootstrap(request: Request) -> dict[str, Any]:
    user = _session_user(request)
    return {
        "brand": {"name": "A-RAG", "product": "Agentic Knowledge Base"},
        "session": {
            "authenticated": user is not None,
            "display_name": user.display_name if user else "",
            "email": user.email if user else "",
            "role": user.role if user else "member",
            "mode": "database" if user else "anonymous",
        },
        "locale": "vi",
        "locales": [
            {"code": "vi", "label": "Tiếng Việt"},
            {"code": "en", "label": "English"},
            {"code": "ja", "label": "日本語"},
        ],
        "capabilities": {
            "document_import": True,
            "document_actions": True,
            "assistant": True,
            "request_hash": "accepted-dev-only",
            "bm25_enabled": settings.neo4j_enabled,
            "vector_enabled": settings.neo4j_enabled and settings.embedding_enabled,
            "graph_enabled": settings.neo4j_enabled,
            "rerank_enabled": False,
            "rerank_provider": "not_configured",
        },
        "landing": {
            "eyebrow": "A-RAG / KNOWLEDGE WORKSPACE",
            "headline": "Turn documents into",
            "headline_accent": "traceable knowledge.",
            "description": "Upload source files, preserve their structure and provenance, then ask grounded questions inside a focused workspace.",
            "primary_action": "Open workspace",
            "secondary_action": "View architecture",
            "features_title": "Built for evidence-first work",
            "features_description": "Every Zone 1 answer is tied back to parsed document elements and validated chunks.",
            "features": [
                {"id": "ingestion", "title": "Structure-aware ingestion", "description": "MinerU or native parsing is selected from profiler metadata."},
                {"id": "lineage", "title": "Provenance preserved", "description": "Element, section and chunk lineage remain queryable."},
                {"id": "assistant", "title": "Grounded assistant", "description": "The current checkpoint retrieves only indexed workspace context."},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/v1/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    user = _session_user(request)
    return {
        "authenticated": user is not None,
        "user": user.as_response() if user else None,
        "csrf_token": _csrf_token(request),
    }


@app.post("/v1/auth/signup")
def signup(payload: SignupRequest, request: Request) -> dict[str, Any]:
    if not payload.accept_terms:
        raise HTTPException(status_code=422, detail="Terms must be accepted")
    try:
        return _auth_response(request, service.create_user(payload.display_name, payload.email, payload.password))
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.post("/v1/auth/signin")
def signin(payload: SigninRequest, request: Request) -> dict[str, Any]:
    try:
        return _auth_response(request, service.authenticate(payload.email, payload.password))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid email or password") from exc


@app.post("/v1/auth/signout")
def signout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"signed_out": True}


@app.post("/v1/auth/forgot-password")
def forgot_password(payload: PasswordResetRequest) -> dict[str, str]:
    # No email provider is configured in Zone 1.  Keep the public response
    # non-enumerating and make the missing external integration explicit.
    return {"message": "If the account exists, a reset link would be sent by the configured mail provider."}


@app.post("/v1/auth/reset-password")
def reset_password(payload: PasswordChangeRequest) -> dict[str, bool]:
    raise HTTPException(status_code=501, detail="Password reset provider is not configured in Zone 1")


@app.put("/v1/preferences/language")
def set_language(payload: LanguageRequest, request: Request) -> dict[str, str]:
    _require_user(request)
    locale = payload.locale.split("-", 1)[0].lower()
    if locale not in {"vi", "en", "ja"}:
        raise HTTPException(status_code=422, detail="Unsupported locale")
    request.session["locale"] = locale
    return {"locale": locale}


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@app.get("/v1/workspaces")
def list_workspaces(request: Request, query: str = "") -> list[dict[str, Any]]:
    user = _require_user(request)
    return [_workspace_response(workspace.workspace_id) for workspace in service.list_workspaces(user.user_id, query)]


@app.post("/v1/workspaces")
def create_workspace(payload: WorkspaceCreateRequest, request: Request) -> dict[str, Any]:
    user = _require_user(request)
    try:
        workspace = service.create_workspace(user.user_id, payload.name)
        return _workspace_response(workspace.workspace_id)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.delete("/v1/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str, payload: WorkspaceDeleteRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    if payload.confirmation != workspace_id:
        raise HTTPException(status_code=422, detail="Confirmation must equal the workspace id")
    service.workspaces.pop(workspace_id, None)
    service.workspace_members.pop(workspace_id, None)
    for document_id in [doc.document_id for doc in service.documents.values() if doc.workspace_id == workspace_id]:
        service.documents.pop(document_id, None)
    for job_id in [job.job_id for job in service.jobs.values() if job.workspace_id == workspace_id]:
        service.jobs.pop(job_id, None)
    return {"workspace_id": workspace_id, "deleted": True}


@app.get("/v1/workspaces/{workspace_id}/overview")
def workspace_overview(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    return service.overview(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/members")
def workspace_members(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    members = []
    for user_id, role in service.workspace_members.get(workspace_id, {}).items():
        user = service.get_user_by_id(user_id)
        if user:
            members.append({"id": user.user_id, "display_name": user.display_name, "role": role, "status": "active"})
    return {"workspace_id": workspace_id, "members": members}


@app.get("/v1/workspaces/{workspace_id}/settings")
def workspace_settings(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    return {
        "workspace_id": workspace_id,
        "retrieval": {
            "top_k": settings.retrieval_top_k,
            "vector_enabled": settings.neo4j_enabled and settings.embedding_enabled,
            "graph_enabled": settings.neo4j_enabled,
            "rerank_enabled": False,
            "rerank_provider": "not_configured",
            "rerank_top_n": 0,
            "min_rerank_score": 0.0,
            "bm25_enabled": settings.neo4j_enabled,
        },
        "storage": {"source_root": str(service.root), "upload_root": str(service.root / "workspaces" / workspace_id)},
    }


@app.get("/v1/workspaces/{workspace_id}/graph")
def workspace_graph(
    workspace_id: str,
    request: Request,
    node_limit: int = Query(default=120, ge=1, le=120),
    edge_limit: int = Query(default=240, ge=1, le=240),
    include_attributes: bool = True,
) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    graph = service.workspace_graph(workspace_id)
    graph["nodes"] = graph["nodes"][:node_limit]
    graph["edges"] = graph["edges"][:edge_limit]
    graph["node_count"] = len(graph["nodes"])
    graph["edge_count"] = len(graph["edges"])
    if not include_attributes:
        for node in graph["nodes"]:
            node.pop("attributes", None)
        for edge in graph["edges"]:
            edge.pop("attributes", None)
    return graph


@app.get("/v1/workspaces/{workspace_id}/graph/graphml")
def workspace_graphml(workspace_id: str, request: Request) -> Response:
    _require_workspace(request, workspace_id)
    graph = service.workspace_graph(workspace_id)
    node_xml = "".join(
        f'<node id="{node["id"]}"><data key="name">{node.get("attributes", {}).get("name", node["id"])}</data></node>'
        for node in graph["nodes"]
    )
    graphml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
        '<key id="name" for="node" attr.name="name" attr.type="string"/>'
        f'<graph id="{workspace_id}" edgedefault="directed">{node_xml}</graph></graphml>'
    )
    return Response(
        content=graphml,
        media_type="application/graphml+xml",
        headers={"Content-Disposition": f'attachment; filename="{workspace_id}-graph.graphml"'},
    )


# ---------------------------------------------------------------------------
# Documents and ingestion checkpoints
# ---------------------------------------------------------------------------


@app.get("/v1/documents")
def documents(
    request: Request,
    workspace_id: str,
    query: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> list[dict[str, Any]]:
    _require_workspace(request, workspace_id)
    return service.list_documents(workspace_id, query=query, offset=offset, limit=limit)


@app.post("/v1/workspaces/{workspace_id}/documents")
async def upload_document(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    filename = request.query_params.get("filename", "document")
    content_type = request.query_params.get("content_type") or request.headers.get("content-type", "")
    relative_path = request.query_params.get("relative_path", "")
    replace_document_id = request.query_params.get("replace_document_id", "")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    try:
        stored = service.upload_bytes(
            workspace_id,
            filename,
            content_type,
            body,
            relative_path=relative_path,
            replace_document_id=replace_document_id,
        )
        record = service.document_record(stored)
        record["replaced_document_id"] = replace_document_id or None
        return record
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/ingestion/jobs/{job_id}")
def ingestion_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_user(request)
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    _require_workspace(request, job.workspace_id)
    return {
        "job_id": job.job_id,
        "project_id": job.workspace_id,
        "document_id": job.document_id,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "flow_stage": job.flow_stage,
        "flow_event_count": len(job.flow_events),
        "error": job.error,
    }


@app.get("/v1/ingestion/status")
def ingestion_status(
    request: Request,
    workspace_id: str,
    document_id: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    try:
        return service.ingestion_status(workspace_id, document_id=document_id, job_id=job_id)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/ingestion/jobs/{job_id}/events")
def ingestion_events(
    job_id: str,
    request: Request,
    workspace_id: str,
    after: int = 0,
) -> dict[str, Any]:
    """Read the ordered LangGraph node/tool/progress events for one job."""

    _require_workspace(request, workspace_id)
    try:
        return service.ingestion_events(workspace_id, job_id=job_id, after=after)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/documents/{document_id}")
def get_document(document_id: str, request: Request, workspace_id: str | None = None) -> dict[str, Any]:
    _require_user(request)
    try:
        document = service.find_document(document_id, workspace_id)
        _require_workspace(request, document.workspace_id)
        return service.document_record(document)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.patch("/v1/documents/{document_id}")
def rename_document(document_id: str, payload: RenameDocumentRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, payload.workspace_id)
    try:
        document = service.find_document(document_id, payload.workspace_id)
        document.name = Path(payload.name).name
        return service.document_record(document)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/documents/{document_id}/content")
def document_content(document_id: str, request: Request, workspace_id: str | None = None) -> Response:
    _require_user(request)
    try:
        document = service.find_document(document_id, workspace_id)
        _require_workspace(request, document.workspace_id)
        return Response(
            content=document.source_bytes,
            media_type=document.content_type,
            headers={"Content-Disposition": f'attachment; filename="{document.name}"'},
        )
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/documents/{document_id}/preview")
def document_preview(document_id: str, request: Request, workspace_id: str | None = None) -> dict[str, Any]:
    _require_user(request)
    try:
        document = service.find_document(document_id, workspace_id)
        _require_workspace(request, document.workspace_id)
        return service.preview(document)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/documents/{document_id}/review")
def document_review(document_id: str, request: Request, workspace_id: str | None = None) -> dict[str, Any]:
    """Return parser elements and normalized layout boxes for the review UI."""
    _require_user(request)
    try:
        document = service.find_document(document_id, workspace_id)
        _require_workspace(request, document.workspace_id)
        return service.review(document)
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/workspaces/{workspace_id}/documents/{document_id}/metadata")
def document_metadata(workspace_id: str, document_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    try:
        return service.metadata(service.find_document(document_id, workspace_id))
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.post("/v1/documents/actions/sync-up")
def sync_documents(payload: SyncDocumentsRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, payload.workspace_id)
    documents_to_sync = _document_ids_for_workspace(payload.workspace_id, payload.document_ids)
    result = service.start_ingestion(
        payload.workspace_id,
        [document.document_id for document in documents_to_sync],
    )
    return {
        "workspace_id": payload.workspace_id,
        "accepted": result["accepted"],
        "skipped": result["skipped"],
        "jobs": [
            {
                **job,
                "status_url": f"/v1/ingestion/jobs/{job['job_id']}",
                "files_url": f"/v1/documents?workspace_id={payload.workspace_id}",
            }
            for job in result["jobs"]
        ],
    }


@app.post("/v1/documents/actions/move")
def move_documents(payload: MoveDocumentsRequest, request: Request) -> list[dict[str, Any]]:
    _require_workspace(request, payload.workspace_id)
    _require_workspace(request, payload.destination_workspace_id)
    moved = _document_ids_for_workspace(payload.workspace_id, payload.document_ids)
    for document in moved:
        document.workspace_id = payload.destination_workspace_id
        document.source_path = f"uploads/{document.name}"
    return [service.document_record(document) for document in moved]


@app.post("/v1/documents/actions/delete")
def delete_documents(payload: SyncDocumentsRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, payload.workspace_id)
    documents_to_delete = _document_ids_for_workspace(payload.workspace_id, payload.document_ids)
    deleted = [document.document_id for document in documents_to_delete]
    for document_id in deleted:
        document = service.documents.pop(document_id, None)
        if document and document.job_id:
            service.jobs.pop(document.job_id, None)
    return {"workspace_id": payload.workspace_id, "deleted": deleted}


# ---------------------------------------------------------------------------
# Assistant/query and chat
# ---------------------------------------------------------------------------


def _assistant_catalog() -> dict[str, Any]:
    tools = [
        {
            "name": "search_project_knowledge",
            "command": "/search",
            "title": "Search project knowledge",
            "description": "Search indexed chunks in the current workspace.",
            "agent_description": "Use for evidence discovery before answering.",
            "use_when": "The user asks to find relevant information.",
            "do_not_use_when": "No workspace is selected.",
            "required_arguments": ["message"],
            "placement": "composer",
            "result_type": "search_results",
            "composer_enabled": True,
            "priority": "primary",
        },
        {
            "name": "get_evidence",
            "command": "/evidence",
            "title": "Get evidence",
            "description": "Resolve a chunk id to its exact indexed source content and lineage.",
            "agent_description": "Use when the user requests a source passage.",
            "use_when": "A chunk/reference id is available.",
            "do_not_use_when": "The evidence id is missing.",
            "required_arguments": ["evidence_id"],
            "placement": "composer",
            "result_type": "evidence",
            "composer_enabled": True,
            "priority": "secondary",
        },
        {
            "name": "answer_project_question",
            "command": "/answer",
            "title": "Answer project question",
            "description": "Answer using deterministic grounded matches from indexed chunks.",
            "agent_description": "Use for a final answer after retrieving evidence.",
            "use_when": "The user asks a question about workspace documents.",
            "do_not_use_when": "The workspace has no indexed context.",
            "required_arguments": ["message"],
            "placement": "composer",
            "result_type": "answer",
            "composer_enabled": True,
            "priority": "primary",
        },
    ]
    return {"version": 1, "tools": tools, "composer_tools": tools}


@app.post("/v1/query")
def query(payload: QueryRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, payload.workspace_id)
    try:
        return service.query(
            payload.workspace_id,
            payload.question,
            file_paths=payload.file_paths,
            conversation_history=payload.conversation_history,
            chat_session_id=payload.chat_session_id,
            save_history=payload.save_history,
        )
    except Exception as exc:
        raise _handle_error(exc) from exc


@app.get("/v1/workspaces/{workspace_id}/assistant/tools")
def assistant_tools(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    return _assistant_catalog()


@app.post("/v1/workspaces/{workspace_id}/assistant/tool-executions")
def assistant_tool_execution(workspace_id: str, payload: AssistantToolRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    started = datetime.now(timezone.utc)
    message = payload.message or str(payload.arguments.get("message") or "")
    result_type = "answer"
    if payload.force_tool == "get_evidence":
        evidence_id = str(payload.arguments.get("evidence_id") or "")
        for document in service.documents.values():
            if document.workspace_id != workspace_id:
                continue
            for chunk in document.chunks:
                if chunk.chunk_id == evidence_id:
                    result_type = "evidence"
                    result = {
                        "evidence_id": evidence_id,
                        "source": document.source_path,
                        "content": chunk.content,
                        "citation": {"chunk_id": chunk.chunk_id, **chunk.metadata.model_dump(mode="json")},
                    }
                    return {
                        "execution_id": f"exec_{uuid4().hex[:12]}",
                        "workspace_id": workspace_id,
                        "command": "/evidence " + evidence_id,
                        "forced_tool": payload.force_tool,
                        "result_type": result_type,
                        "result": result,
                        "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                    }
        raise HTTPException(status_code=404, detail="Evidence chunk not found")
    if not message:
        raise HTTPException(status_code=422, detail="Tool message is required")
    response = service.query(
        workspace_id,
        message,
        file_paths=[str(value) for value in payload.filters.get("file_paths", [])] if isinstance(payload.filters.get("file_paths"), list) else None,
        chat_session_id=payload.chat_session_id,
        save_history=payload.save_history,
    )
    if payload.force_tool == "search_project_knowledge":
        result_type = "search_results"
        result = {"references": response["citations"], "units": response["citations"]}
    else:
        result = response
    return {
        "execution_id": f"exec_{uuid4().hex[:12]}",
        "workspace_id": workspace_id,
        "command": payload.force_tool,
        "forced_tool": payload.force_tool,
        "result_type": result_type,
        "result": result,
        "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }


@app.get("/v1/workspaces/{workspace_id}/chat/sessions")
def chat_sessions(workspace_id: str, request: Request, limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    sessions = [
        service.chat_summary(session)
        for session in service.chat_sessions.values()
        if session["workspace_id"] == workspace_id
    ]
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"workspace_id": workspace_id, "sessions": sessions[:limit]}


@app.get("/v1/workspaces/{workspace_id}/chat/sessions/{session_id}")
def chat_session(workspace_id: str, session_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    session = service.chat_sessions.get(session_id)
    if not session or session["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {**service.chat_summary(session), "turns": session["turns"]}


@app.post("/v1/answers/feedback")
def answer_feedback(payload: AnswerFeedbackRequest, request: Request) -> dict[str, Any]:
    _require_workspace(request, payload.project_id)
    key = payload.idempotency_key or f"{payload.answer_id}:{payload.rating}"
    if key in service.feedback:
        return {"feedback_id": service.feedback[key]["feedback_id"], "answer_id": payload.answer_id, "created": False}
    feedback_id = f"fb_{uuid4().hex[:12]}"
    service.feedback[key] = {
        "feedback_id": feedback_id,
        "answer_id": payload.answer_id,
        "project_id": payload.project_id,
        "rating": payload.rating,
        "reason_codes": [],
        "comment": None,
        "triage_status": "new",
        "admin_note": None,
        "version": 1,
        "created_at": _now(),
        "updated_at": _now(),
    }
    return {"feedback_id": feedback_id, "answer_id": payload.answer_id, "created": True}


# ---------------------------------------------------------------------------
# Admin and MCP contract surfaces (local Zone 1 implementations)
# ---------------------------------------------------------------------------


def _admin_user_record(user) -> dict[str, Any]:
    memberships = [
        {"workspace_id": workspace_id, "role": members[user.user_id]}
        for workspace_id, members in service.workspace_members.items()
        if user.user_id in members
    ]
    return {
        **user.as_response(),
        "workspace_count": len(memberships),
        "memberships": memberships,
    }


@app.get("/v1/admin/overview")
def admin_overview(request: Request) -> dict[str, int]:
    _admin_required(request)
    users = service.list_users()
    return {
        "total_users": len(users),
        "active_users": sum(user.is_active for user in users),
        "global_admins": sum(user.role == "admin" for user in users),
        "total_workspaces": len(service.workspaces),
        "total_memberships": sum(len(members) for members in service.workspace_members.values()),
    }


@app.get("/v1/admin/users")
def admin_users(request: Request) -> list[dict[str, Any]]:
    _admin_required(request)
    return [_admin_user_record(user) for user in service.list_users()]


@app.patch("/v1/admin/users/{user_id}")
def update_admin_user(user_id: str, payload: AdminUserUpdateRequest, request: Request) -> dict[str, Any]:
    _admin_required(request)
    user = service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role is not None and payload.role not in {"admin", "member"}:
        raise HTTPException(status_code=422, detail="Unsupported global role")
    updated = service.update_user(user_id, role=payload.role, is_active=payload.is_active)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _admin_user_record(updated)


@app.get("/v1/admin/workspaces")
def admin_workspaces(request: Request) -> list[dict[str, Any]]:
    _admin_required(request)
    result = []
    for workspace in service.workspaces.values():
        members = service.workspace_members.get(workspace.workspace_id, {})
        role_counts = {"owner": 0, "editor": 0, "viewer": 0}
        for role in members.values():
            role_counts[role if role in role_counts else "viewer"] += 1
        result.append({**_workspace_response(workspace.workspace_id), "member_count": len(members), "role_counts": role_counts})
    return result


def _feedback_records() -> list[dict[str, Any]]:
    return list(service.feedback.values())


@app.get("/v1/admin/feedback/summary")
def admin_feedback_summary(request: Request) -> dict[str, Any]:
    _admin_required(request)
    records = _feedback_records()
    return {
        "feedback_total": len(records),
        "negative_total": sum(item["rating"] == "negative" for item in records),
        "unresolved_total": sum(item["triage_status"] not in {"resolved", "dismissed"} for item in records),
        "reason_counts": {},
    }


@app.get("/v1/admin/feedback")
def admin_feedback(request: Request) -> list[dict[str, Any]]:
    _admin_required(request)
    return _feedback_records()


@app.patch("/v1/admin/feedback/{feedback_id}")
def update_admin_feedback(feedback_id: str, payload: AdminFeedbackUpdateRequest, request: Request) -> dict[str, Any]:
    _admin_required(request)
    record = next((item for item in service.feedback.values() if item["feedback_id"] == feedback_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Feedback not found")
    if payload.expected_version != record["version"]:
        raise HTTPException(status_code=409, detail="Feedback version conflict")
    record["triage_status"] = payload.status
    record["admin_note"] = payload.admin_note
    record["version"] += 1
    record["updated_at"] = _now()
    return record


@app.put("/v1/admin/users/{user_id}/workspaces/{workspace_id}")
def set_workspace_role(user_id: str, workspace_id: str, payload: WorkspaceRoleRequest, request: Request) -> dict[str, Any]:
    _admin_required(request)
    if service.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    service.workspace(workspace_id)
    if payload.role not in {"owner", "editor", "viewer"}:
        raise HTTPException(status_code=422, detail="Unsupported workspace role")
    service.workspace_members.setdefault(workspace_id, {})[user_id] = payload.role
    return {"user_id": user_id, "workspace_id": workspace_id, "role": payload.role}


@app.delete("/v1/admin/users/{user_id}/workspaces/{workspace_id}")
def revoke_workspace_role(user_id: str, workspace_id: str, request: Request) -> dict[str, bool]:
    _admin_required(request)
    service.workspace_members.get(workspace_id, {}).pop(user_id, None)
    return {"revoked": True}


def _issued_mcp_credential(request: Request, payload: McpCredentialRequest, *, issued_for=None) -> dict[str, Any]:
    _require_workspace(request, payload.workspace_id)
    now = datetime.now(timezone.utc)
    credential_id = f"mcp_{uuid4().hex[:12]}"
    api_key = f"ar_{uuid4().hex}{uuid4().hex}"
    expires = now.timestamp() + payload.ttl_days * 86400
    credential = {
        "id": credential_id,
        "name": payload.name,
        "scopes": ["query", "documents:read"],
        "project_ids": [payload.workspace_id],
        "status": "active",
        "created_at": now.isoformat(),
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        "last_used_at": None,
        "workspace_role": "owner",
    }
    _mcp_credentials[credential_id] = {**credential, "api_key": api_key}
    result = {
        "api_key": api_key,
        "sdk_config": {"base_url": "http://127.0.0.1:8010", "api_key": api_key, "project_id": payload.workspace_id},
        "credential": credential,
        "connection": {
            "server_name": "a-rag",
            "transport": "streamable-http",
            "url": "http://127.0.0.1:8001/mcp",
            "authorization": "Bearer",
            "api_key_env": "A_RAG_API_KEY",
            "workspace_id": payload.workspace_id,
            "workspace_role": "owner",
            "sdk": {"python_package": "mcp", "python_module": "mcp", "client_class": "ClientSession"},
            "config": {"mcpServers": {"a-rag": {"command": "python", "args": ["-m", "src.mcp_server"]}}},
        },
    }
    if issued_for:
        result["issued_for"] = issued_for.as_response()
        result["issued_for"]["global_role"] = issued_for.role
    return result


@app.get("/v1/mcp/credentials")
def mcp_credentials(request: Request) -> list[dict[str, Any]]:
    user = _require_user(request)
    allowed = {workspace.workspace_id for workspace in service.list_workspaces(user.user_id)}
    return [
        {key: value for key, value in item.items() if key != "api_key"}
        for item in _mcp_credentials.values()
        if set(item["project_ids"]) & allowed
    ]


@app.post("/v1/mcp/credentials")
def issue_mcp_credential(payload: McpCredentialRequest, request: Request) -> dict[str, Any]:
    return _issued_mcp_credential(request, payload)


@app.delete("/v1/mcp/credentials/{credential_id}")
def revoke_mcp_credential(credential_id: str, request: Request) -> dict[str, bool]:
    _require_user(request)
    item = _mcp_credentials.get(credential_id)
    if item:
        item["status"] = "revoked"
    return {"revoked": True}


@app.post("/v1/admin/mcp/credentials")
def issue_admin_mcp_credential(payload: AdminMcpCredentialRequest, request: Request) -> dict[str, Any]:
    admin = _admin_required(request)
    target = service.get_user_by_id(payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found")
    return _issued_mcp_credential(request, payload, issued_for=target)


# ---------------------------------------------------------------------------
# Evaluation surface: run the live query path, save trace, and optionally score
# ---------------------------------------------------------------------------


_EVALUATION_METRICS = ["faithfulness", "response_relevancy", "context_precision", "context_recall"]
_EVALUATION_MAX_ROWS = 200
_EVALUATION_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_UNCONFIGURED_LLM_KEYS = {"", "sk-mock-key-replace-with-actual", "sk-mock-placeholder-key", "[REDACTED:openai-key]"}


def _evaluation_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key != "rows"}


def _xlsx_column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha()).upper()
    result = 0
    for character in letters:
        result = result * 26 + ord(character) - ord("A") + 1
    return max(result - 1, 0)


def _read_evaluation_xlsx(content: bytes) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            relation_targets = {
                item.attrib["Id"]: item.attrib["Target"]
                for item in relationships.findall(f"{{{_PKG_REL_NS}}}Relationship")
            }
            first_sheet = workbook.find(f"{{{_XLSX_NS}}}sheets/{{{_XLSX_NS}}}sheet")
            if first_sheet is None:
                raise ValueError("XLSX does not contain a worksheet")
            relation_id = first_sheet.attrib.get(f"{{{_REL_NS}}}id", "")
            target = relation_targets.get(relation_id)
            if not target:
                raise ValueError("XLSX worksheet relationship is missing")
            sheet_path = target.lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = posixpath.normpath(posixpath.join("xl", sheet_path))
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(text.text or "" for text in item.iter(f"{{{_XLSX_NS}}}t"))
                    for item in strings.findall(f"{{{_XLSX_NS}}}si")
                ]
            sheet = ET.fromstring(archive.read(sheet_path))
            matrix: list[list[str]] = []
            for row in sheet.findall(f".//{{{_XLSX_NS}}}sheetData/{{{_XLSX_NS}}}row"):
                values: dict[int, str] = {}
                for cell in row.findall(f"{{{_XLSX_NS}}}c"):
                    index = _xlsx_column_index(cell.attrib.get("r", "A"))
                    cell_type = cell.attrib.get("t", "")
                    if cell_type == "inlineStr":
                        value = "".join(text.text or "" for text in cell.iter(f"{{{_XLSX_NS}}}t"))
                    else:
                        raw = cell.find(f"{{{_XLSX_NS}}}v")
                        value = raw.text if raw is not None and raw.text is not None else ""
                        if cell_type == "s" and value:
                            value = shared_strings[int(value)]
                    values[index] = value
                if values:
                    matrix.append([values.get(index, "") for index in range(max(values) + 1)])
    except (KeyError, zipfile.BadZipFile, ET.ParseError, IndexError) as exc:
        raise ValueError("Could not read the uploaded XLSX evaluation file") from exc
    if not matrix:
        return []
    headers = [value.strip().lower() for value in matrix[0]]
    return [
        {header: row[index].strip() if index < len(row) else "" for index, header in enumerate(headers) if header}
        for row in matrix[1:]
        if any(value.strip() for value in row)
    ]


def _read_evaluation_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    if not content:
        raise ValueError("The evaluation upload is empty")
    if len(content) > _EVALUATION_MAX_UPLOAD_BYTES:
        raise ValueError("Evaluation upload exceeds the 10 MB limit")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            reader = csv.DictReader(StringIO(content.decode("utf-8-sig"), newline=""))
            rows = [
                {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}
                for row in reader
            ]
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError("CSV must be valid UTF-8 with a header row") from exc
    elif suffix == ".xlsx":
        rows = _read_evaluation_xlsx(content)
    else:
        raise ValueError("Evaluation files must use .csv or .xlsx")
    if len(rows) > _EVALUATION_MAX_ROWS:
        raise ValueError(f"Evaluation files may contain at most {_EVALUATION_MAX_ROWS} questions")
    if not rows:
        raise ValueError("The evaluation file does not contain question rows")
    if not any("question" in row for row in rows):
        raise ValueError("The evaluation file must include a 'question' column")
    normalized = []
    for index, row in enumerate(rows, start=1):
        question = (row.get("question") or "").strip()
        if not question:
            raise ValueError(f"Question is empty on data row {index + 1}")
        if len(question) > 16000:
            raise ValueError(f"Question exceeds 16,000 characters on data row {index + 1}")
        reference = (row.get("reference_answer") or "").strip() or None
        if reference and len(reference) > 16000:
            raise ValueError(f"Reference answer exceeds 16,000 characters on data row {index + 1}")
        normalized.append({"question": question, "reference_answer": reference})
    return normalized


def _evaluation_trace(result: dict[str, Any]) -> dict[str, Any]:
    citations = result.get("citations", [])
    retrieval = result.get("retrieval_trace", {}) or {}
    graph_context = result.get("graph_context", []) or []
    final_contexts: list[dict[str, Any]] = []
    dense_contexts: list[dict[str, Any]] = []
    hybrid_contexts: list[dict[str, Any]] = []
    for rank, citation in enumerate(citations, start=1):
        metadata = citation.get("metadata", {}) or {}
        item = {
            "chunk_id": citation.get("reference_id"),
            "rank": rank,
            "content": citation.get("content", ""),
            "document_id": citation.get("document_id"),
            "source_path": citation.get("source_path") or citation.get("file_path"),
            "dense_score": metadata.get("dense_score"),
            "bm25_score": metadata.get("bm25_score"),
            "rrf_score": metadata.get("rrf_score"),
            "graph_score": metadata.get("graph_score"),
            "rerank_score": metadata.get("rerank_score"),
            "selected_for_answer": True,
        }
        final_contexts.append(item)
        if item["dense_score"] is not None:
            dense_contexts.append(item)
        if item["dense_score"] is not None or item["bm25_score"] is not None:
            hybrid_contexts.append(item)
    graph_items = [
        {
            "chunk_id": item.get("chunk_id"),
            "rank": index,
            "content": json.dumps(item, ensure_ascii=False),
            "document_id": item.get("document_id"),
            "source_path": item.get("source_path"),
            "graph_score": item.get("score"),
            "selected_for_answer": True,
        }
        for index, item in enumerate(graph_context, start=1)
    ]
    has_rerank = any(item.get("rerank_score") is not None for item in final_contexts)
    return {
        "vector_db": dense_contexts,
        "hybrid_retrieval": hybrid_contexts or final_contexts,
        "graph_search": graph_items,
        "reranking": [item for item in final_contexts if item.get("rerank_score") is not None],
        "final_contexts": final_contexts,
        "availability": {
            "vector_db": "available" if dense_contexts else "no_vector_results",
            "hybrid_retrieval": "available" if hybrid_contexts else "scores_unavailable",
            "graph_search": "available" if graph_items else "no_graph_results",
            "reranking": "available" if has_rerank else "not_configured_or_no_scores",
        },
        "pipeline": {
            "retrieval": retrieval,
            "attempt_history": result.get("attempt_history", []),
            "run_status": result.get("run_status"),
            "provenance_validation": result.get("provenance_validation", {}),
        },
    }


def _judge_evaluation_answer(question: str, reference: str, answer: str, contexts: list[dict[str, Any]], metrics: list[str]) -> dict[str, float]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from src.core.llm_client import get_chat_llm

    if (settings.openai_api_key or "").strip() in _UNCONFIGURED_LLM_KEYS:
        raise ValueError("Evaluation judge requires a configured OPENAI_API_KEY")

    metric_definitions = {
        "faithfulness": "How much of the generated answer is supported by the retrieved contexts?",
        "response_relevancy": "How directly and completely does the generated answer address the question?",
        "context_precision": "What fraction of the retrieved contexts are useful evidence for the question and reference answer?",
        "context_recall": "How much information needed for the reference answer appears in the retrieved contexts?",
    }
    context_text = "\n\n".join(
        f"[{index}] {item.get('content', '')[:1800]}"
        for index, item in enumerate(contexts[:8], start=1)
    )
    requested = {metric: metric_definitions[metric] for metric in metrics}
    response = get_chat_llm(temperature=0, max_tokens=500).invoke([
        SystemMessage(content=(
            "You are an evaluation judge for a retrieval-augmented assistant. "
            "Score each requested metric from 0.0 to 1.0 using only the supplied question, reference answer, answer, and contexts. "
            "Do not reward unsupported claims. Return only a JSON object whose keys are the requested metric names and whose values are numbers."
        )),
        HumanMessage(content=json.dumps({
            "question": question,
            "reference_answer": reference,
            "generated_answer": answer,
            "retrieved_contexts": context_text,
            "metrics": requested,
        }, ensure_ascii=False)),
    ])
    raw = getattr(response, "content", "")
    if isinstance(raw, list):
        raw = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in raw)
    raw = str(raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Evaluation judge did not return a JSON score object")
    parsed = json.loads(raw[start : end + 1])
    scores: dict[str, float] = {}
    for metric in metrics:
        value = float(parsed[metric])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"Evaluation judge returned an invalid {metric} score")
        scores[metric] = round(value, 4)
    return scores


def _run_evaluation_job(
    job_id: str,
    row_ids: list[str] | None = None,
    failed_only: bool = False,
    metrics: list[str] | None = None,
    generate_answers: bool = False,
) -> None:
    job = _evaluation_jobs.get(job_id)
    if not job:
        return
    selected_ids = set(row_ids or [])
    score_metrics = [metric for metric in (metrics or _EVALUATION_METRICS) if metric in _EVALUATION_METRICS]
    job["status"] = "running" if generate_answers else job.get("status", "completed")
    if not failed_only and not selected_ids:
        score_rows = job["rows"]
    else:
        score_rows = [
            row for row in job["rows"]
            if (not selected_ids or row["case_id"] in selected_ids)
            and (not failed_only or row.get("status") == "failed" or row.get("score_error") or any(row.get("scores", {}).get(metric) is None for metric in score_metrics))
        ]
    job["score_status"] = "scoring" if any(row.get("reference_answer") for row in score_rows) else (
        "manual_review" if job.get("evaluation_mode") == "manual_review" else "no_ground_truth"
    )
    if generate_answers:
        job["started_at"] = job.get("started_at") or _now()
        job["judge_model"] = (
            settings.primary_llm_model
            if (settings.openai_api_key or "").strip() not in _UNCONFIGURED_LLM_KEYS
            else None
        )
    job["updated_at"] = _now()
    service.persist_state()

    for row in score_rows:
        needs_query = generate_answers or row.get("status") == "failed"
        if needs_query:
            row["status"] = "running"
            row["error_code"] = None
            row["error_message"] = None
            service.persist_state()
            started = datetime.now(timezone.utc)
            try:
                result = service.query(
                    job["workspace_id"],
                    row["question"],
                    save_history=False,
                )
                row["generated_answer"] = result.get("answer", "")
                row["answer_id"] = result.get("answer_id")
                row["trace"] = _evaluation_trace(result)
                row["duration_ms"] = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
                row["status"] = "completed"
            except Exception as exc:
                row["status"] = "failed"
                row["error_code"] = type(exc).__name__
                row["error_message"] = str(exc)
                row["duration_ms"] = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
                service.persist_state()
                continue

        reference = row.get("reference_answer")
        contexts = (row.get("trace") or {}).get("final_contexts", [])
        if not reference:
            row["evaluation_mode"] = "trace_only"
            continue
        if not row.get("generated_answer") or not contexts:
            row["evaluation_mode"] = "trace_only"
            row["score_error"] = "No generated answer or retrieved context is available to score."
            continue
        row["evaluation_mode"] = "grounded"
        if not score_metrics:
            row["score_error"] = "Select at least one metric before scoring."
            continue
        try:
            scores = _judge_evaluation_answer(
                row["question"], reference, row["generated_answer"], contexts, score_metrics
            )
            row.setdefault("scores", {}).update(scores)
            row["score_error"] = None
        except Exception as exc:
            row["score_error"] = f"{type(exc).__name__}: {exc}"
        job["updated_at"] = _now()
        service.persist_state()

    completed = sum(row.get("status") == "completed" for row in job["rows"])
    failed = sum(row.get("status") == "failed" for row in job["rows"])
    scored = sum(any(value is not None for value in row.get("scores", {}).values()) for row in job["rows"])
    trace_only = sum(row.get("evaluation_mode") == "trace_only" for row in job["rows"])
    score_errors = sum(bool(row.get("score_error")) for row in job["rows"] if row.get("reference_answer"))
    job.update({
        "status": "failed" if completed == 0 and failed else "completed",
        "completed_rows": completed,
        "failed_rows": failed,
        "scored_rows": scored,
        "trace_only_rows": trace_only,
        "updated_at": _now(),
        "finished_at": _now(),
    })
    if not any(row.get("reference_answer") for row in job["rows"]):
        job["score_status"] = "manual_review"
    elif scored:
        job["score_status"] = "completed_with_errors" if score_errors or failed else "completed"
    elif completed and trace_only == completed:
        job["score_status"] = "no_context"
    elif score_errors:
        job["score_status"] = "completed_with_errors"
    else:
        job["score_status"] = "no_scoreable_rows"
    service.persist_state()


@app.post("/v1/workspaces/{workspace_id}/evaluations")
async def create_evaluation(workspace_id: str, request: Request, filename: str = "evaluation.csv") -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    try:
        questions = _read_evaluation_rows(filename, await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = [
        {
            "case_id": f"case_{index:04d}",
            "question": item["question"],
            "reference_answer": item["reference_answer"],
            "generated_answer": None,
            "answer_id": None,
            "status": "queued",
            "evaluation_mode": "grounded" if item["reference_answer"] else "trace_only",
            "duration_ms": None,
            "trace": None,
            "scores": {metric: None for metric in _EVALUATION_METRICS},
            "error_code": None,
            "error_message": None,
            "score_error": None,
        }
        for index, item in enumerate(questions, start=1)
    ]
    has_reference = [bool(row["reference_answer"]) for row in rows]
    evaluation_mode = "manual_review" if not any(has_reference) else "auto_score" if all(has_reference) else "mixed"
    job_id = f"eval_{uuid4().hex[:12]}"
    now = _now()
    job = {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "filename": Path(filename).name,
        "status": "queued",
        "score_status": "manual_review" if evaluation_mode == "manual_review" else "scoring",
        "evaluation_mode": evaluation_mode,
        "total_rows": len(rows),
        "completed_rows": 0,
        "failed_rows": 0,
        "scored_rows": 0,
        "trace_only_rows": 0,
        "display_metrics": list(_EVALUATION_METRICS),
        "judge_model": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "rows": rows,
    }
    _evaluation_jobs[job_id] = job
    service.persist_state()
    try:
        service.submit_evaluation(_run_evaluation_job, job_id, None, False, list(_EVALUATION_METRICS), True)
    except Exception as exc:
        job.update({"status": "failed", "score_status": "failed", "error_message": str(exc), "finished_at": _now()})
        service.persist_state()
    return _evaluation_summary(job)


@app.get("/v1/workspaces/{workspace_id}/evaluation-template")
def evaluation_template(workspace_id: str, request: Request) -> Response:
    _require_workspace(request, workspace_id)
    # A tiny valid XLSX package generated in memory; no filesystem side effect.
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="evaluation" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>question</t></is></c><c r="B1" t="inlineStr"><is><t>reference_answer</t></is></c></row></sheetData></worksheet>',
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="mau-cham-diem-cau-tra-loi.xlsx"'},
    )


def _get_eval(workspace_id: str, job_id: str, request: Request) -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    job = _evaluation_jobs.get(job_id)
    if not job or job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Evaluation job not found")
    return job


@app.get("/v1/workspaces/{workspace_id}/evaluations/{job_id}")
def get_evaluation(workspace_id: str, job_id: str, request: Request) -> dict[str, Any]:
    return _evaluation_summary(_get_eval(workspace_id, job_id, request))


@app.get("/v1/workspaces/{workspace_id}/evaluations/{job_id}/rows")
def evaluation_rows(workspace_id: str, job_id: str, request: Request, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    job = _get_eval(workspace_id, job_id, request)
    rows = job["rows"][offset : offset + limit]
    return {"job_id": job_id, "workspace_id": workspace_id, "offset": offset, "limit": limit, "total": len(job["rows"]), "display_metrics": job["display_metrics"], "rows": rows}


@app.post("/v1/workspaces/{workspace_id}/evaluations/{job_id}/score")
def score_evaluation(workspace_id: str, job_id: str, payload: EvaluationScoreRequest, request: Request) -> dict[str, Any]:
    job = _get_eval(workspace_id, job_id, request)
    selected_metrics = [metric for metric in (payload.display_metrics or job["display_metrics"]) if metric in _EVALUATION_METRICS]
    if not selected_metrics:
        raise HTTPException(status_code=422, detail="Select at least one supported evaluation metric")
    if job.get("evaluation_mode") == "manual_review":
        raise HTTPException(status_code=409, detail="This evaluation has no reference answers to score")
    job["display_metrics"] = selected_metrics
    job["score_status"] = "scoring"
    job["updated_at"] = _now()
    service.persist_state()
    service.submit_evaluation(
        _run_evaluation_job,
        job_id,
        payload.row_ids or None,
        payload.failed_only,
        selected_metrics,
        False,
    )
    return _evaluation_summary(job)


@app.patch("/v1/workspaces/{workspace_id}/evaluations/{job_id}/display-metrics")
def update_evaluation_metrics(workspace_id: str, job_id: str, payload: EvaluationMetricsRequest, request: Request) -> dict[str, Any]:
    job = _get_eval(workspace_id, job_id, request)
    metrics = [metric for metric in payload.display_metrics if metric in _EVALUATION_METRICS]
    if not metrics:
        raise HTTPException(status_code=422, detail="Select at least one supported evaluation metric")
    job["display_metrics"] = metrics
    job["updated_at"] = _now()
    service.persist_state()
    return _evaluation_summary(job)


def _export_cell(reference: str, value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = str(value if value is not None else "")
    text = "".join(character for character in text if character in "\t\n\r" or ord(character) >= 32)
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _evaluation_export_rows(job: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers = [
        "case_id", "question", "reference_answer", "generated_answer", "status", "evaluation_mode",
        "duration_ms", *_EVALUATION_METRICS, "score_error", "error_code", "error_message", "trace_json",
    ]
    rows = []
    for row in job["rows"]:
        exported = {key: row.get(key) for key in headers if key not in _EVALUATION_METRICS and key != "trace_json"}
        exported.update(row.get("scores", {}))
        exported["trace_json"] = json.dumps(row.get("trace"), ensure_ascii=False) if row.get("trace") else ""
        rows.append(exported)
    return headers, rows


def _build_evaluation_xlsx(headers: list[str], rows: list[dict[str, Any]]) -> bytes:
    def column_name(index: int) -> str:
        name = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    xml_rows = [
        "<row r=\"1\">" + "".join(_export_cell(f"{column_name(i)}1", value) for i, value in enumerate(headers, start=1)) + "</row>"
    ]
    for row_number, item in enumerate(rows, start=2):
        cells = []
        for column, header in enumerate(headers, start=1):
            value = item.get(header)
            cells.append(_export_cell(f"{column_name(column)}{row_number}", value))
        xml_rows.append(f'<row r="{row_number}">' + "".join(cells) + "</row>")
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="evaluation" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>",
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in parts.items():
            archive.writestr(path, content)
    return output.getvalue()


@app.get("/v1/workspaces/{workspace_id}/evaluations/{job_id}/export")
def export_evaluation(workspace_id: str, job_id: str, request: Request, format: str = "xlsx") -> Response:
    job = _get_eval(workspace_id, job_id, request)
    if format not in {"xlsx", "csv"}:
        raise HTTPException(status_code=422, detail="Export format must be xlsx or csv")
    headers, rows = _evaluation_export_rows(job)
    if format == "xlsx":
        content = _build_evaluation_xlsx(headers, rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        download_name = f"{job_id}.xlsx"
    else:
        text_output = BytesIO()
        text_stream = StringIO(newline="")
        writer = csv.DictWriter(text_stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        with zipfile.ZipFile(text_output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{job_id}.csv", text_stream.getvalue().encode("utf-8-sig"))
        content = text_output.getvalue()
        media_type = "application/zip"
        download_name = f"{job_id}.zip"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{download_name}"'})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host=settings.api_host, port=settings.api_port, reload=False)


__all__ = ["app", "service"]
