"""Contract tests for the imported web application against the current Zone 1 BE."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.service import WebApplicationService
from src.core.config import settings


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "neo4j_enabled", False)
    monkeypatch.setattr(settings, "embedding_enabled", False)
    web_service = WebApplicationService(tmp_path / "web")
    monkeypatch.setattr(main, "service", web_service)
    with TestClient(main.app) as test_client:
        yield test_client


def signup(client: TestClient) -> tuple[str, dict]:
    response = client.post(
        "/v1/auth/signup",
        json={
            "display_name": "Zone One",
            "email": "zone-one@example.com",
            "password": "SecurePassword123!",
            "accept_terms": True,
        },
    )
    assert response.status_code == 200
    workspace = client.get("/v1/workspaces").json()[0]
    return workspace["workspace_id"], response.json()


def test_web_bootstrap_and_auth_boundary(client: TestClient) -> None:
    bootstrap = client.get("/v1/ui/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["capabilities"]["document_import"] is True
    assert client.get("/v1/workspaces").status_code == 401

    workspace_id, auth = signup(client)
    assert auth["authenticated"] is True
    assert auth["csrf_token"]
    assert workspace_id.startswith("ws_")


def test_upload_runs_parser_chunking_and_provenance_contract(client: TestClient) -> None:
    workspace_id, _ = signup(client)
    upload = client.post(
        f"/v1/workspaces/{workspace_id}/documents",
        params={"filename": "architecture.md", "content_type": "text/markdown"},
        content=b"# Ingestion\n\nMinerU keeps layout; the chunk keeps lineage.",
    )
    assert upload.status_code == 200, upload.text
    document = upload.json()
    assert document["status"] == "uploaded"
    assert document["upload_action"] == "created"
    assert document["source_path"] == "uploads/architecture.md"

    document_id = document["id"]
    uploaded_status = client.get(
        "/v1/ingestion/status",
        params={"workspace_id": workspace_id, "document_id": document_id},
    )
    assert uploaded_status.json()["status"] == "uploaded"
    assert uploaded_status.json()["readiness"] == "not_ready"

    start = client.post(
        "/v1/documents/actions/sync-up",
        json={"workspace_id": workspace_id, "document_ids": [document_id]},
    )
    assert start.status_code == 200, start.text
    assert start.json()["accepted"] == [document_id]
    assert start.json()["jobs"][0]["status"] == "processing"

    for _ in range(100):
        status_response = client.get(
            "/v1/ingestion/status",
            params={"workspace_id": workspace_id, "document_id": document_id},
        )
        if status_response.json()["status"] == "indexed":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"ingestion did not complete: {status_response.json()}")

    status_response = client.get(
        "/v1/ingestion/status",
        params={"workspace_id": workspace_id, "document_id": document_id},
    )
    assert status_response.json()["readiness"] == "ready"
    assert status_response.json()["stage"] == "indexed"
    assert status_response.json()["chunk_count"] >= 1

    metadata = client.get(f"/v1/workspaces/{workspace_id}/documents/{document_id}/metadata").json()
    lineage = metadata["metadata"]["lineage"]
    assert lineage["element_chunk_map"]
    assert all(chunk_ids for chunk_ids in lineage["element_chunk_map"].values())

    preview = client.get(
        f"/v1/documents/{document_id}/preview",
        params={"workspace_id": workspace_id},
    ).json()
    assert preview["preview_available"] is True
    assert "MinerU keeps layout" in preview["preview"]


def test_query_and_assistant_return_grounded_chunk_references(client: TestClient) -> None:
    workspace_id, _ = signup(client)
    client.post(
        f"/v1/workspaces/{workspace_id}/documents",
        params={"filename": "facts.txt", "content_type": "text/plain"},
        content=b"The provenance gate validates every chunk before indexing.",
    )
    document_id = client.get("/v1/documents", params={"workspace_id": workspace_id}).json()[0]["id"]
    start = client.post(
        "/v1/documents/actions/sync-up",
        json={"workspace_id": workspace_id, "document_ids": [document_id]},
    )
    assert start.status_code == 200
    for _ in range(100):
        if client.get(
            "/v1/ingestion/status",
            params={"workspace_id": workspace_id, "document_id": document_id},
        ).json()["status"] == "indexed":
            break
        time.sleep(0.01)

    answer = client.post(
        "/v1/query",
        json={"workspace_id": workspace_id, "question": "provenance gate"},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["citations"]
    chunk_id = body["citations"][0]["reference_id"]
    assert chunk_id in body["answer"]

    catalog = client.get(f"/v1/workspaces/{workspace_id}/assistant/tools")
    assert catalog.status_code == 200
    assert {item["name"] for item in catalog.json()["composer_tools"]} == {
        "search_project_knowledge",
        "get_evidence",
        "answer_project_question",
    }
    evidence = client.post(
        f"/v1/workspaces/{workspace_id}/assistant/tool-executions",
        json={"force_tool": "get_evidence", "arguments": {"evidence_id": chunk_id}},
    )
    assert evidence.status_code == 200
    assert evidence.json()["result_type"] == "evidence"
    assert evidence.json()["result"]["citation"]["chunk_id"] == chunk_id
