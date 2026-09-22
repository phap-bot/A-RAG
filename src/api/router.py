"""Zone 3 streaming routes for the user-facing Agentic RAG API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.api.stream_handler import stream_agent_events
from src.core.config import settings


router = APIRouter(prefix="/api", tags=["agent-stream"])


class ChatStreamRequest(BaseModel):
    """Validated input required to start one Agentic RAG stream."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=16000)
    file_paths: list[str] = Field(default_factory=list)
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    chat_session_id: str | None = None
    save_history: bool = True
    max_retries: int = Field(default=settings.max_reflection_retries, ge=1, le=5)


@router.post("/chat/stream")
async def chat_stream(payload: ChatStreamRequest, request: Request) -> StreamingResponse:
    """Stream AgentThought, tool, and final-response events to the frontend."""
    # Importing here avoids coupling router import to the application singleton
    # and keeps graph/model initialization out of API module import time.
    from src.api.main import _require_workspace, service
    from src.agents.orchestrator.graph import agentic_rag_app
    from src.agents.orchestrator.state import create_initial_agent_state

    _require_workspace(request, payload.workspace_id)
    service.configure_agentic_retrieval()
    state = create_initial_agent_state(
        query=payload.question,
        history=payload.conversation_history,
        max_retries=payload.max_retries,
        workspace_id=payload.workspace_id,
        file_paths=payload.file_paths,
        max_agent_handoffs=settings.max_agent_handoffs,
        max_tool_rounds=settings.max_tool_rounds_per_agent,
    )

    def finalize(final_state: dict[str, Any]) -> dict[str, Any]:
        return service.agentic_response_from_state(
            payload.workspace_id,
            payload.question,
            final_state,
            chat_session_id=payload.chat_session_id,
            save_history=payload.save_history,
        )

    return StreamingResponse(
        stream_agent_events(agentic_rag_app, state, on_complete=finalize),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["ChatStreamRequest", "router"]
