"""FastAPI compatibility boundary for the imported A-RAG web application.

This module is deliberately thin.  The API layer owns HTTP/session concerns;
``WebApplicationService`` owns the Zone 1 workflow and its contracts:

    HTTP upload -> profiler -> MinerU/native parser -> skills -> chunk graph
                 -> provenance validator -> document/status/metadata response

The UI/session shell remains process-local for the development milestone;
document indexing and Zone 2 retrieval can be switched to the configured
Neo4j repository. Source bytes and parsed artifacts remain real at every
checkpoint, while durable auth and background workers are separate concerns.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from typing import Any
from uuid import uuid4
import zipfile

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from src.api.service import StoredDocument, WebApplicationService
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key="a-rag-development-session-key-change-before-production",
    same_site="lax",
    https_only=False,
)

service = WebApplicationService()
_mcp_credentials: dict[str, dict[str, Any]] = {}
_evaluation_jobs: dict[str, dict[str, Any]] = {}


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
    return service.users.get(str(user_id)) if user_id else None


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
        "zone": "zone_2" if settings.neo4j_enabled else "zone_1",
        "mineru_base_url": settings.mineru_base_url,
        "storage_backend": "neo4j" if settings.neo4j_enabled else "local_memory",
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
        user = service.users.get(user_id)
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
        "is_active": True,
        "workspace_count": len(memberships),
        "memberships": memberships,
    }


@app.get("/v1/admin/overview")
def admin_overview(request: Request) -> dict[str, int]:
    _admin_required(request)
    return {
        "total_users": len(service.users),
        "active_users": len(service.users),
        "global_admins": sum(user.role == "admin" for user in service.users.values()),
        "total_workspaces": len(service.workspaces),
        "total_memberships": sum(len(members) for members in service.workspace_members.values()),
    }


@app.get("/v1/admin/users")
def admin_users(request: Request) -> list[dict[str, Any]]:
    _admin_required(request)
    return [_admin_user_record(user) for user in service.users.values()]


@app.patch("/v1/admin/users/{user_id}")
def update_admin_user(user_id: str, payload: AdminUserUpdateRequest, request: Request) -> dict[str, Any]:
    _admin_required(request)
    user = service.users.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role in {"admin", "member"}:
        user.role = payload.role
    return _admin_user_record(user)


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
    if user_id not in service.users:
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
    target = service.users.get(payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found")
    return _issued_mcp_credential(request, payload, issued_for=target)


# ---------------------------------------------------------------------------
# Evaluation surface: trace-only local contract until Zone 4 scoring is wired
# ---------------------------------------------------------------------------


def _evaluation_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key != "rows"}


@app.post("/v1/workspaces/{workspace_id}/evaluations")
async def create_evaluation(workspace_id: str, request: Request, filename: str = "evaluation.csv") -> dict[str, Any]:
    _require_workspace(request, workspace_id)
    await request.body()  # Keep the raw upload boundary compatible with the UI.
    job_id = f"eval_{uuid4().hex[:12]}"
    now = _now()
    job = {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "filename": filename,
        "status": "completed",
        "score_status": "no_scoreable_rows",
        "evaluation_mode": "trace_only",
        "total_rows": 0,
        "completed_rows": 0,
        "failed_rows": 0,
        "scored_rows": 0,
        "trace_only_rows": 0,
        "display_metrics": ["faithfulness", "response_relevancy", "context_precision", "context_recall"],
        "judge_model": None,
        "error_message": "Zone 4 evaluation scoring is not connected in this checkpoint.",
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "finished_at": now,
        "rows": [],
    }
    _evaluation_jobs[job_id] = job
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
    job["display_metrics"] = payload.display_metrics or job["display_metrics"]
    job["updated_at"] = _now()
    return _evaluation_summary(job)


@app.patch("/v1/workspaces/{workspace_id}/evaluations/{job_id}/display-metrics")
def update_evaluation_metrics(workspace_id: str, job_id: str, payload: EvaluationMetricsRequest, request: Request) -> dict[str, Any]:
    job = _get_eval(workspace_id, job_id, request)
    job["display_metrics"] = payload.display_metrics
    job["updated_at"] = _now()
    return _evaluation_summary(job)


@app.get("/v1/workspaces/{workspace_id}/evaluations/{job_id}/export")
def export_evaluation(workspace_id: str, job_id: str, request: Request, format: str = "xlsx") -> Response:
    job = _get_eval(workspace_id, job_id, request)
    if format not in {"xlsx", "csv"}:
        raise HTTPException(status_code=422, detail="Export format must be xlsx or csv")
    content = json.dumps(job["rows"], ensure_ascii=False).encode("utf-8")
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx" else "application/zip"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{job_id}.{format}"'})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host=settings.api_host, port=settings.api_port, reload=False)


__all__ = ["app", "service"]
