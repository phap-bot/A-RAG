"""Translate LangGraph v2 stream events into the Zone 3 SSE contract."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from src.api.schemas import (
    AgentThought,
    FinalResponse,
    MessageChunk,
    SSEPayload,
    StreamError,
    ToolResult,
    ToolStart,
)


_AGENT_NODES = {
    "agent_main": "Main agent coordinating the workspace request",
    "query_formulator": "Query formulation started",
    "parallel_retriever": "Parallel retrieval started",
    "synthesizer": "Grounded answer synthesis started",
    "critic_reflection": "Answer validation started",
}
_RAG_TOOL_NAMES = {
    "search_knowledge_base",
    "query_knowledge_graph",
    "get_evidence",
    "handoff_to_query_formulator",
    "handoff_to_parallel_retriever",
    "handoff_to_synthesizer",
    "handoff_to_critic_reflection",
    "handoff_to_main",
}


def encode_sse(payload: SSEPayload) -> str:
    """Encode one validated payload as an SSE data frame."""
    body = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"data: {body}\n\n"


async def stream_agent_events(
    app: Any,
    input_state: Mapping[str, Any],
    *,
    on_complete: Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]] | None = None,
) -> AsyncIterator[str]:
    """Consume a LangGraph v2 event stream and yield UI-safe SSE frames.

    The handler exposes node progress, tool boundaries, and generated answer
    deltas. It never exposes hidden chain-of-thought text; ``AgentThought`` is
    a safe execution-progress event. Tool output is serialized without
    changing structured citation/artifact fields.
    """
    started_tool_calls: set[str] = set()
    thought_buffers: dict[str, str] = {}
    emitted_response_text = False
    completed_state_seen = False
    try:
        async for event in app.astream_events(input_state, version="v2"):
            event_name = str(event.get("event", ""))
            run_id = _optional_string(event.get("run_id"))
            name = str(event.get("name", ""))
            data = event.get("data") or {}

            if event_name == "on_chain_start" and name in _AGENT_NODES:
                yield encode_sse(
                    AgentThought(
                        node=name,
                        message=_AGENT_NODES[name],
                        run_id=run_id,
                    )
                )
                continue

            if event_name == "on_chat_model_end":
                node = _langgraph_node(event)
                if node == "synthesizer":
                    continue
                if node not in {"query_formulator", "critic_reflection"}:
                    continue
                thought_key = _event_key(event)
                buffered_text = thought_buffers.pop(thought_key, "")
                text = _text_from_value(data.get("output")) or buffered_text
                if text and _looks_like_json(text):
                    yield encode_sse(
                        AgentThought(
                            node=node or "agent",
                            message="Planner metadata is available for audit",
                            details=_parse_structured_text(text),
                            run_id=run_id,
                        )
                    )
                continue

            if event_name == "on_tool_start":
                tool_calls = _tool_calls_from_value(data.get("input"))
                if not tool_calls and name in _RAG_TOOL_NAMES:
                    tool_calls = [{"name": name, "args": data.get("input"), "id": None}]
                for tool_call in tool_calls:
                    tool_key = _tool_call_key(tool_call)
                    if tool_key in started_tool_calls:
                        continue
                    started_tool_calls.add(tool_key)
                    yield encode_sse(
                        ToolStart(
                            tool_name=tool_call["name"],
                            arguments=_tool_arguments(tool_call.get("args")),
                            tool_call_id=_optional_string(tool_call.get("id")),
                            run_id=run_id,
                        )
                    )
                continue

            if event_name == "on_chat_model_stream":
                chunk = data.get("chunk")
                for tool_call in _tool_calls_from_value(chunk):
                    tool_key = _tool_call_key(tool_call)
                    if tool_key in started_tool_calls:
                        continue
                    started_tool_calls.add(tool_key)
                    yield encode_sse(
                        ToolStart(
                            tool_name=tool_call["name"],
                            arguments=_tool_arguments(tool_call.get("args")),
                            tool_call_id=_optional_string(tool_call.get("id")),
                            run_id=run_id,
                        )
                    )

                text = _text_from_value(chunk)
                if text:
                    node = _langgraph_node(event)
                    thought_key = _event_key(event)
                    is_planner_metadata = node not in {"", "synthesizer"} or thought_key in thought_buffers or _looks_like_json(text)
                    if not is_planner_metadata:
                        emitted_response_text = True
                        yield encode_sse(MessageChunk(content=text, run_id=run_id))
                    else:
                        thought_buffers.setdefault(thought_key, "")
                        thought_buffers[thought_key] += text
                continue

            if event_name == "on_tool_end":
                output = data.get("output")
                tool_name = name or _tool_name_from_value(output) or "tool"
                yield encode_sse(
                    ToolResult(
                        tool_name=tool_name,
                        output=_json_safe(output),
                        run_id=run_id,
                    )
                )
                continue

            if event_name == "on_chain_end":
                final_state = _agent_state_from_chain_output(data.get("output"))
                if final_state is None or completed_state_seen:
                    continue
                completed_state_seen = True
                completed_response = await _complete_response(final_state, on_complete)
                final_text = str(
                    completed_response.get("answer")
                    or final_state.get("synthesized_response")
                    or ""
                )
                metadata = _final_response_metadata(completed_response)
                if final_text and not emitted_response_text:
                    yield encode_sse(
                        MessageChunk(content=final_text, run_id=run_id, **metadata)
                    )
                    emitted_response_text = True
                elif metadata:
                    # Token streaming already delivered the answer text. Send
                    # the structured result separately without duplicating it.
                    yield encode_sse(
                        FinalResponse(content="", run_id=run_id, **metadata)
                    )

        yield encode_sse(FinalResponse(content="", done=True))
    except Exception as exc:
        yield encode_sse(
            StreamError(
                message=str(exc) or "Agent stream failed",
                error_type=type(exc).__name__,
                retryable=isinstance(exc, (TimeoutError, asyncio.TimeoutError)),
            )
        )


def _tool_calls_from_value(value: Any) -> list[dict[str, Any]]:
    """Read tool-call metadata from an AIMessageChunk or provider dictionary."""
    if value is None:
        return []
    candidates = getattr(value, "tool_call_chunks", None)
    if candidates is None:
        candidates = getattr(value, "tool_calls", None)
    if candidates is None and isinstance(value, Mapping):
        candidates = value.get("tool_call_chunks") or value.get("tool_calls")
    if not isinstance(candidates, list):
        return []

    calls: list[dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            name = candidate.get("name")
            args = candidate.get("args")
            call_id = candidate.get("id")
        else:
            name = getattr(candidate, "name", None)
            args = getattr(candidate, "args", None)
            call_id = getattr(candidate, "id", None)
        if name:
            calls.append({"name": str(name), "args": args, "id": call_id})
    return calls


def _tool_call_key(tool_call: Mapping[str, Any]) -> str:
    """Build a stable de-duplication key for streamed tool-call fragments."""
    return str(tool_call.get("id") or tool_call.get("name") or "tool")


def _tool_arguments(value: Any) -> dict[str, Any]:
    """Return complete JSON arguments, or an empty object for partial fragments."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _text_from_value(value: Any) -> str:
    """Extract text from a streamed message chunk without stringifying metadata."""
    content = getattr(value, "content", None)
    if content is None and isinstance(value, Mapping):
        content = value.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def _agent_state_from_chain_output(value: Any) -> Mapping[str, Any] | None:
    """Recognize the compiled graph's final AgentState output."""
    if not isinstance(value, Mapping):
        return None
    required_state_keys = {"query", "retrieved_docs", "critique", "synthesized_response"}
    if not required_state_keys.issubset(value):
        return None
    return value


def _langgraph_node(event: Mapping[str, Any]) -> str:
    """Read the LangGraph node tag attached to a child model run."""
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping):
        node = metadata.get("langgraph_node") or metadata.get("graph_node")
        if node:
            return str(node)
    return ""


def _event_key(event: Mapping[str, Any]) -> str:
    """Build a stable buffer key for one model run."""
    return str(event.get("run_id") or event.get("name") or "agent")


def _parse_structured_text(value: str) -> Any:
    """Parse planner JSON for audit, retaining raw text when parsing fails."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _looks_like_json(value: str) -> bool:
    """Identify planner JSON fragments before a complete model event exists."""
    return value.lstrip().startswith(("{", "["))


async def _complete_response(
    final_state: Mapping[str, Any],
    on_complete: Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]] | None,
) -> Mapping[str, Any]:
    """Finalize the graph output once and normalize an optional callback result."""
    if on_complete is None:
        return {}
    result = on_complete(final_state)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, Mapping) else {}


def _final_response_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Select only fields allowed in the structured SSE final event."""
    keys = (
        "answer_id",
        "citations",
        "confidence",
        "chat_session",
        "retrieval_trace",
        "provenance_validation",
        "attempt_history",
        "agent_handoffs",
        "run_status",
    )
    return {key: _json_safe(value[key]) for key in keys if value.get(key) is not None}


def _tool_name_from_value(value: Any) -> str:
    """Recover a tool name from a structured tool output when the event omits it."""
    if isinstance(value, Mapping):
        name = value.get("tool_name")
        return str(name) if name else ""
    return ""


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _json_safe(value: Any) -> Any:
    """Preserve JSON-shaped tool output and serialize known model objects."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


__all__ = ["encode_sse", "stream_agent_events"]
