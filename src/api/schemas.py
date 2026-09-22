"""Strict SSE payload contracts shared by the API stream and the web client."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class _SSEPayload(BaseModel):
    """Common validation policy for every JSON object sent over SSE."""

    model_config = ConfigDict(extra="forbid")


class AgentThought(_SSEPayload):
    """A safe node update with optional structured audit details.

    ``details`` contains planner/tool metadata only. Hidden chain-of-thought is
    never copied into this field.
    """

    event: Literal["agent_thought"] = "agent_thought"
    node: str = Field(min_length=1, description="LangGraph node that emitted the update")
    message: str = Field(min_length=1, description="Safe user-facing progress message")
    details: Any | None = Field(default=None, description="Structured planner metadata for optional audit")
    run_id: str | None = Field(default=None, description="LangChain run identifier")


class ToolStart(_SSEPayload):
    """Notification that the agent selected a tool and execution has started."""

    event: Literal["tool_start"] = "tool_start"
    tool_name: str = Field(min_length=1, description="Registered LangChain tool name")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Validated tool arguments")
    tool_call_id: str | None = Field(default=None, description="Provider tool-call identifier")
    run_id: str | None = Field(default=None, description="LangChain run identifier")


class ToolResult(_SSEPayload):
    """Raw, structured result returned by a tool for UI inspection."""

    event: Literal["tool_result"] = "tool_result"
    tool_name: str = Field(min_length=1, description="Registered LangChain tool name")
    output: Any = Field(description="Unmodified tool output, including citations or artifacts")
    tool_call_id: str | None = Field(default=None, description="Provider tool-call identifier")
    run_id: str | None = Field(default=None, description="LangChain run identifier")


class FinalResponse(_SSEPayload):
    """Incremental text plus optional structured data for the completed answer.

    Token events only carry ``content``. The final graph event may also carry
    the normalized response envelope so the frontend does not need to issue a
    second query just to obtain citations or persist a chat session.
    """

    event: Literal["final_response"] = "final_response"
    content: str = Field(description="Generated response delta or final text")
    done: bool = Field(default=False, description="True only for the terminal response event")
    run_id: str | None = Field(default=None, description="LangChain run identifier")
    answer_id: str | None = Field(default=None, description="Stable answer identifier")
    citations: list[dict[str, Any]] | None = Field(default=None, description="Grounding citations")
    confidence: dict[str, Any] | None = Field(default=None, description="Answer confidence envelope")
    chat_session: dict[str, Any] | None = Field(default=None, description="Updated chat session summary")
    retrieval_trace: dict[str, Any] | None = Field(default=None, description="Retrieval execution trace")
    provenance_validation: dict[str, Any] | None = Field(default=None, description="Provenance gate result")
    attempt_history: list[dict[str, Any]] | None = Field(default=None, description="Critic outcomes across candidate attempts")
    agent_handoffs: int | None = Field(default=None, description="Specialist handoff count")
    run_status: str | None = Field(default=None, description="Completed or best-effort graph result")


class MessageChunk(_SSEPayload):
    """A stream delta from the final synthesizer response only."""

    event: Literal["message_chunk"] = "message_chunk"
    content: str = Field(description="Markdown-safe final answer delta")
    run_id: str | None = Field(default=None, description="LangChain run identifier")
    answer_id: str | None = Field(default=None, description="Stable answer identifier")
    citations: list[dict[str, Any]] | None = Field(default=None, description="Grounding citations")
    confidence: dict[str, Any] | None = Field(default=None, description="Answer confidence envelope")
    chat_session: dict[str, Any] | None = Field(default=None, description="Updated chat session summary")
    retrieval_trace: dict[str, Any] | None = Field(default=None, description="Retrieval execution trace")
    provenance_validation: dict[str, Any] | None = Field(default=None, description="Provenance gate result")
    attempt_history: list[dict[str, Any]] | None = Field(default=None, description="Critic outcomes across candidate attempts")
    agent_handoffs: int | None = Field(default=None, description="Specialist handoff count")
    run_status: str | None = Field(default=None, description="Completed or best-effort graph result")


class StreamError(_SSEPayload):
    """Terminal stream error that can be rendered without parsing an exception."""

    event: Literal["error"] = "error"
    message: str = Field(min_length=1, description="Safe error message for the client")
    error_type: str = Field(min_length=1, description="Stable exception class or domain error type")
    retryable: bool = False
    run_id: str | None = Field(default=None, description="LangChain run identifier")


SSEPayload: TypeAlias = AgentThought | ToolStart | ToolResult | MessageChunk | FinalResponse | StreamError


__all__ = [
    "AgentThought",
    "FinalResponse",
    "MessageChunk",
    "SSEPayload",
    "StreamError",
    "ToolResult",
    "ToolStart",
]
