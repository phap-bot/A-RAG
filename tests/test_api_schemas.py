"""Contract tests for the Zone 3 SSE payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas import AgentThought, FinalResponse, MessageChunk, StreamError, ToolResult, ToolStart


def test_sse_payloads_have_discriminating_event_names() -> None:
    assert AgentThought(node="retriever", message="Searching").event == "agent_thought"
    assert ToolStart(tool_name="search_knowledge_base").event == "tool_start"
    assert ToolResult(tool_name="search_knowledge_base", output={"hits": []}).event == "tool_result"
    assert FinalResponse(content="answer", done=True).event == "final_response"
    assert MessageChunk(content="delta").event == "message_chunk"
    assert StreamError(message="timeout", error_type="TimeoutError").event == "error"


def test_tool_result_preserves_raw_structured_output() -> None:
    payload = ToolResult(
        tool_name="search_knowledge_base",
        output={"citations": [{"chunk_id": "chunk-1"}], "score": 0.9},
    )

    assert payload.model_dump(mode="json")["output"] == {
        "citations": [{"chunk_id": "chunk-1"}],
        "score": 0.9,
    }


def test_sse_payload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentThought(node="router", message="deciding", unexpected="value")
