"""Contract tests for the imported web application against the current Zone 1 BE."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.service import StoredDocument, WebApplicationService
from src.core.config import settings
from src.ingestion.parser.models import BoundingBox, ElementMetadata, ParsedDocument, ParsedElement


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


def test_local_frontend_cors_preflight_accepts_vite_port(client: TestClient) -> None:
    response = client.options(
        "/v1/ui/bootstrap",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-request-timestamp,x-request-nonce,x-request-signature",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


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
    event_response = client.get(
        f"/v1/ingestion/jobs/{document['job_id']}/events",
        params={"workspace_id": workspace_id},
    )
    assert event_response.status_code == 200
    event_names = [event["event"] for event in event_response.json()["events"]]
    assert "node_started" in event_names
    assert "node_completed" in event_names
    assert any(event.get("node") == "plan_strategy" for event in event_response.json()["events"])

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

    review = client.get(
        f"/v1/documents/{document_id}/review",
        params={"workspace_id": workspace_id},
    )
    assert review.status_code == 200, review.text
    review_body = review.json()
    assert review_body["review_available"] is True
    assert review_body["total_pages"] == 1
    assert review_body["elements"]
    assert review_body["pages"][0]["page_number"] == 1
    assert review_body["elements"][0]["element_id"]
    assert "bounding_box" in review_body["elements"][0]
    assert review_body["has_bounding_boxes"] is False


def test_document_review_requires_workspace_scope(client: TestClient) -> None:
    workspace_id, _ = signup(client)
    upload = client.post(
        f"/v1/workspaces/{workspace_id}/documents",
        params={"filename": "review.txt", "content_type": "text/plain"},
        content=b"Review me",
    )
    assert upload.status_code == 200
    document_id = upload.json()["id"]

    response = client.get(f"/v1/documents/{document_id}/review")
    assert response.status_code == 200
    assert response.json()["review_available"] is False


def test_service_review_projects_normalized_bounding_boxes(tmp_path: Path) -> None:
    document = StoredDocument(
        document_id="doc-review",
        workspace_id="ws-review",
        name="review.pdf",
        source_path="uploads/review.pdf",
        content_type="application/pdf",
        source_bytes=b"pdf",
        parsed_document=ParsedDocument(
            document_id="doc-review",
            file_name="review.pdf",
            file_type="pdf",
            total_pages=1,
            elements=[
                ParsedElement(
                    element_id="doc-review-elem-0000",
                    content="Detected title",
                    metadata=ElementMetadata(
                        source_doc="review.pdf",
                        page_number=1,
                        element_index=0,
                        element_type="header",
                        bounding_box=BoundingBox(x1=0.1, y1=0.2, x2=0.9, y2=0.3),
                        confidence=0.96,
                    ),
                ),
            ],
        ),
    )
    body = WebApplicationService(tmp_path / "web").review(document)
    assert body["has_bounding_boxes"] is True
    assert body["pages"][0]["elements"][0]["bounding_box"] == {
        "x1": 0.1,
        "y1": 0.2,
        "x2": 0.9,
        "y2": 0.3,
    }


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


def test_ingestion_failure_is_exposed_as_failed_and_retryable(client: TestClient) -> None:
    workspace_id, _ = signup(client)
    upload = client.post(
        f"/v1/workspaces/{workspace_id}/documents",
        params={"filename": "unsupported.bin", "content_type": "application/octet-stream"},
        content=b"not a supported document",
    )
    assert upload.status_code == 200
    document = upload.json()
    assert document["status"] == "uploaded"

    start = client.post(
        "/v1/documents/actions/sync-up",
        json={"workspace_id": workspace_id, "document_ids": [document["id"]]},
    )
    assert start.status_code == 200

    for _ in range(100):
        ingestion = client.get(
            "/v1/ingestion/status",
            params={"workspace_id": workspace_id, "document_id": document["id"]},
        ).json()
        if ingestion["status"] == "failed":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"unsupported ingestion did not fail: {ingestion}")

    assert ingestion["readiness"] == "failed"
    assert ingestion["retryable"] is True
    assert ingestion["error"]["error_type"] == "IngestionError"

    job = client.get(f"/v1/ingestion/jobs/{document['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "failed"
    assert job.json()["error"]["error_type"] == "IngestionError"
