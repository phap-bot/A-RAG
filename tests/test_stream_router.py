"""Contract test for the FastAPI SSE endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.service import WebApplicationService
from src.core.config import settings


class _FakeGraph:
    async def astream_events(self, _state: Any, *, version: str):
        assert version == "v2"
        yield {
            "event": "on_chain_start",
            "name": "query_formulator",
            "run_id": "node-1",
            "data": {},
        }
        yield {
            "event": "on_chat_model_stream",
            "name": "ChatModel",
            "run_id": "model-1",
            "data": {"chunk": {"content": "streamed answer"}},
        }


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "neo4j_enabled", False)
    monkeypatch.setattr(settings, "embedding_enabled", False)
    web_service = WebApplicationService(tmp_path / "web")
    monkeypatch.setattr(main, "service", web_service)
    with TestClient(main.app) as test_client:
        yield test_client


def _signup(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/signup",
        json={
            "display_name": "Stream User",
            "email": "stream-user@example.com",
            "password": "SecurePassword123!",
            "accept_terms": True,
        },
    )
    assert response.status_code == 200
    return client.get("/v1/workspaces").json()[0]["workspace_id"]


def test_chat_stream_returns_sse_frames_and_authenticates_workspace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = _signup(client)

    from src.agents.orchestrator import graph

    monkeypatch.setattr(graph, "agentic_rag_app", _FakeGraph())
    response = client.post(
        "/api/chat/stream",
        json={"workspace_id": workspace_id, "question": "What is indexed?"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"event":"agent_thought"' in response.text
    assert '"event":"final_response"' in response.text
    assert '"done":true' in response.text


def test_chat_stream_projects_one_completed_graph_result_into_final_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = _signup(client)

    class CompletedGraph:
        async def astream_events(self, _state: Any, *, version: str):
            assert version == "v2"
            yield {
                "event": "on_chain_end",
                "name": "LangGraph",
                "run_id": "graph-1",
                "data": {
                    "output": {
                        "query": "What is indexed?",
                        "retrieved_docs": [],
                        "critique": None,
                        "synthesized_response": "Grounded answer",
                    }
                },
            }

    def finalize(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"chat_session_id": "chat-1", "save_history": True}
        return {
            "answer": "Grounded answer",
            "answer_id": "ans-1",
            "citations": [{"reference_id": "chunk-1"}],
            "confidence": {"score": 0.9},
            "chat_session": {"id": "chat-1"},
        }

    monkeypatch.setattr(main.service, "agentic_response_from_state", finalize)
    from src.agents.orchestrator import graph

    monkeypatch.setattr(graph, "agentic_rag_app", CompletedGraph())
    response = client.post(
        "/api/chat/stream",
        json={
            "workspace_id": workspace_id,
            "question": "What is indexed?",
            "chat_session_id": "chat-1",
        },
    )

    assert response.status_code == 200, response.text
    assert '"answer_id":"ans-1"' in response.text
    assert '"reference_id":"chunk-1"' in response.text
    assert '"id":"chat-1"' in response.text
