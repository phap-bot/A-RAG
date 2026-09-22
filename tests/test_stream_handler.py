"""Contract tests for LangGraph-to-SSE event translation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.api.stream_handler import stream_agent_events


class _FakeGraph:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def astream_events(self, _input: Any, *, version: str):
        assert version == "v2"
        for event in self.events:
            yield event


def _payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frames = asyncio.run(_collect(_FakeGraph(events)))
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


async def _collect(graph: _FakeGraph) -> list[str]:
    return [frame async for frame in stream_agent_events(graph, {"query": "test"})]


def test_stream_handler_emits_thought_tool_result_and_final_response() -> None:
    payloads = _payloads(
        [
            {"event": "on_chain_start", "name": "parallel_retriever", "run_id": "node-1", "data": {}},
            {
                "event": "on_chat_model_stream",
                "name": "ChatModel",
                "run_id": "model-1",
                "data": {"chunk": {"tool_calls": [{"id": "call-1", "name": "search_knowledge_base", "args": {"query": "test"}}]}},
            },
            {"event": "on_tool_end", "name": "search_knowledge_base", "run_id": "tool-1", "data": {"output": {"chunk_id": "chunk-1"}}},
            {"event": "on_chat_model_stream", "name": "ChatModel", "run_id": "model-2", "data": {"chunk": {"content": "Grounded answer"}}},
        ]
    )

    assert [payload["event"] for payload in payloads] == [
        "agent_thought",
        "tool_start",
        "tool_result",
        "message_chunk",
        "final_response",
    ]
    assert payloads[1]["arguments"] == {"query": "test"}
    assert payloads[2]["output"] == {"chunk_id": "chunk-1"}
    assert payloads[3]["content"] == "Grounded answer"
    assert payloads[4]["done"] is True


def test_stream_handler_converts_graph_error_to_terminal_error_event() -> None:
    class FailingGraph:
        async def astream_events(self, _input: Any, *, version: str):
            assert version == "v2"
            raise TimeoutError("model timed out")
            yield  # pragma: no cover

    frames = asyncio.run(_collect(FailingGraph()))
    payloads = [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]

    assert payloads == [
        {
            "event": "error",
            "message": "model timed out",
            "error_type": "TimeoutError",
            "retryable": True,
            "run_id": None,
        }
    ]


def test_stream_handler_sends_structured_completion_without_duplicate_token_text() -> None:
    final_state = {
        "query": "test",
        "retrieved_docs": [],
        "critique": None,
        "synthesized_response": "delta",
    }

    payloads = _payloads(
        [
            {
                "event": "on_chat_model_stream",
                "name": "ChatModel",
                "run_id": "model-1",
                "data": {"chunk": {"content": "delta"}},
            },
            {"event": "on_chain_end", "name": "LangGraph", "run_id": "graph-1", "data": {"output": final_state}},
        ]
    )

    assert [payload["event"] for payload in payloads] == [
        "message_chunk",
        "final_response",
    ]
    assert payloads[0]["content"] == "delta"
    assert payloads[1]["done"] is True


def test_stream_handler_attaches_completion_metadata_from_callback() -> None:
    final_state = {
        "query": "test",
        "retrieved_docs": [],
        "critique": None,
        "synthesized_response": "answer",
    }

    async def collect_with_callback() -> list[str]:
        async def complete(state: dict[str, Any]) -> dict[str, Any]:
            assert state["query"] == "test"
            return {
                "answer": "answer",
                "answer_id": "ans-1",
                "citations": [{"reference_id": "chunk-1"}],
                "confidence": {"score": 0.9},
            }

        return [
            frame
            async for frame in stream_agent_events(
                _FakeGraph([
                    {"event": "on_chain_end", "name": "LangGraph", "data": {"output": final_state}},
                ]),
                {"query": "test"},
                on_complete=complete,
            )
        ]

    payloads = [json.loads(frame.removeprefix("data: ").strip()) for frame in asyncio.run(collect_with_callback())]
    assert payloads[0]["answer_id"] == "ans-1"
    assert payloads[0]["citations"] == [{"reference_id": "chunk-1"}]
    assert payloads[0]["confidence"] == {"score": 0.9}
    assert payloads[-1]["done"] is True


def test_stream_handler_keeps_planner_json_out_of_message_chunks() -> None:
    planner_json = '{"intent":"find the most successful team"}'
    payloads = _payloads(
        [
            {
                "event": "on_chat_model_stream",
                "name": "ChatModel",
                "run_id": "planner-1",
                "metadata": {"langgraph_node": "query_formulator"},
                "data": {"chunk": {"content": planner_json}},
            },
            {
                "event": "on_chat_model_end",
                "name": "ChatModel",
                "run_id": "planner-1",
                "metadata": {"langgraph_node": "query_formulator"},
                "data": {"output": {"content": planner_json}},
            },
        ]
    )

    assert payloads[0]["event"] == "agent_thought"
    assert payloads[0]["details"] == {"intent": "find the most successful team"}
    assert all(payload["event"] != "message_chunk" for payload in payloads)
