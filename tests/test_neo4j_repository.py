"""Contract tests for the Neo4j storage and MCP retrieval boundaries.

These tests deliberately use a driver-shaped fake. They verify the data and
Cypher contract without pretending that a local Neo4j server is running in CI.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.config import settings
from src.core.exceptions import GraphDBError
from src.ingestion.parser.models import (
    BoundingBox,
    ChunkMetadata,
    ContentChunk,
    ElementMetadata,
    ParsedDocument,
    ParsedElement,
)
from src.mcp_server.tools import kb_retrieval_tools
from src.retrieval.embeddings import LocalBGEEmbeddingProvider, build_embedding_provider
from src.retrieval.neo4j_repository import Neo4jRepository


class _FakeResult:
    def consume(self) -> "_FakeResult":
        return self

    def __iter__(self):
        return iter(())


class _FakeTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> _FakeResult:
        self.calls.append((query, parameters))
        return _FakeResult()


class _FakeSession:
    def __init__(self) -> None:
        self.transaction = _FakeTransaction()

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def run(self, query: str, **parameters: Any) -> _FakeResult:
        return self.transaction.run(query, **parameters)

    def execute_write(self, callback, *args: Any) -> None:
        callback(self.transaction, *args)


class _FakeDriver:
    def __init__(self) -> None:
        self.session_instance = _FakeSession()
        self.closed = False

    def verify_connectivity(self) -> None:
        return None

    def session(self, **_kwargs: Any) -> _FakeSession:
        return self.session_instance

    def close(self) -> None:
        self.closed = True


def _document() -> tuple[ParsedDocument, list[ContentChunk]]:
    document_id = "doc-1"
    header = ParsedElement(
        element_id="doc-1-elem-0000",
        content="Introduction",
        metadata=ElementMetadata(
            source_doc="guide.md",
            element_index=0,
            element_type="header",
            header_level=1,
            section_path=["Introduction"],
            bounding_box=BoundingBox(x1=0, y1=0, x2=1, y2=0.1),
        ),
    )
    body = ParsedElement(
        element_id="doc-1-elem-0001",
        content="The provenance contract is immutable.",
        metadata=ElementMetadata(
            source_doc="guide.md",
            element_index=1,
            element_type="text",
            section_path=["Introduction"],
        ),
    )
    document = ParsedDocument(
        document_id=document_id,
        file_name="guide.md",
        file_type="md",
        elements=[header, body],
    )
    chunks = [
        ContentChunk(
            chunk_id="doc-1-chunk-0000",
            content="[Section: Introduction]\nIntroduction",
            metadata=ChunkMetadata(
                document_id=document_id,
                source_doc="guide.md",
                element_ids=[header.element_id],
                section_path=["Introduction"],
                chunk_index=0,
            ),
        ),
        ContentChunk(
            chunk_id="doc-1-chunk-0001",
            content="[Section: Introduction]\nThe provenance contract is immutable.",
            metadata=ChunkMetadata(
                document_id=document_id,
                source_doc="guide.md",
                element_ids=[body.element_id],
                section_path=["Introduction"],
                parent_chunk_id="doc-1-chunk-0000",
                chunk_index=1,
            ),
        ),
    ]
    return document, chunks


def test_schema_and_upsert_scope_ids_by_workspace() -> None:
    driver = _FakeDriver()
    repository = Neo4jRepository(driver=driver)
    repository.ensure_schema()
    schema_calls = [query for query, _ in driver.session_instance.transaction.calls]
    assert any("(c.workspace_id, c.chunk_id)" in query for query in schema_calls)
    assert any("(d.workspace_id, d.document_id)" in query for query in schema_calls)
    assert any(":User" in query and "u.email" in query for query in schema_calls)

    document, chunks = _document()
    result = repository.upsert_document(
        document,
        workspace_id="workspace-a",
        source_path="uploads/guide.md",
        chunks=chunks,
    )
    assert result == {
        "document_id": "doc-1",
        "chunk_count": 2,
        "element_count": 2,
        "section_count": 1,
    }
    write_calls = driver.session_instance.transaction.calls
    assert any("MERGE (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})" in query for query, _ in write_calls)
    assert any("MATCH (parent:Chunk {workspace_id: row.workspace_id" in query for query, _ in write_calls)
    assert all(
        parameters.get("workspace_id") == "workspace-a"
        for query, parameters in write_calls
        if "document_id" in parameters and query.strip().startswith("MATCH")
    )


def test_lineage_projection_keeps_source_metadata() -> None:
    node = {
        "chunk_id": "doc-1-chunk-0001",
        "content": "immutable",
        "workspace_id": "workspace-a",
        "document_id": "doc-1",
        "source_path": "uploads/guide.md",
        "source_doc": "guide.md",
        "element_ids": ["doc-1-elem-0001"],
        "page_numbers": [1],
        "section_path": ["Introduction"],
        "parent_chunk_id": "doc-1-chunk-0000",
        "metadata_json": '{"chunk_strategy": "hierarchical_semantic"}',
    }
    result = Neo4jRepository._chunk_result(node, 0.8, "hybrid")
    assert result["content"] == "immutable"
    assert result["metadata"]["workspace_id"] == "workspace-a"
    assert result["metadata"]["element_ids"] == ["doc-1-elem-0001"]
    assert result["parent_chunk_id"] == "doc-1-chunk-0000"


def test_fulltext_search_uses_non_conflicting_cypher_parameter() -> None:
    driver = _FakeDriver()
    repository = Neo4jRepository(driver=driver)

    repository.search_fulltext(workspace_id="workspace-a", query="provenance gate")

    query, parameters = driver.session_instance.transaction.calls[-1]
    assert "$search_text" in query
    assert "$query" not in query
    assert parameters["search_text"] == "provenance gate"


def test_vector_search_binds_external_query_vector() -> None:
    driver = _FakeDriver()
    repository = Neo4jRepository(driver=driver)

    repository.search_vector(
        workspace_id="workspace-a",
        query_embedding=[0.1, 0.2, 0.3],
        top_k=2,
    )

    query, parameters = driver.session_instance.transaction.calls[-1]
    assert "FOR $query_embedding" in query
    assert parameters["query_embedding"] == [0.1, 0.2, 0.3]


def test_invalid_index_identifier_is_rejected() -> None:
    with pytest.raises(GraphDBError):
        Neo4jRepository._safe_identifier("chunk-index; DROP", "index")


def test_mcp_tool_requires_workspace_and_delegates_to_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRepository:
        def search_hybrid(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["workspace_id"] == "workspace-a"
            return [{"chunk_id": "c1", "content": "source", "metadata": {}}]

    monkeypatch.setattr(kb_retrieval_tools, "_repository", FakeRepository())
    monkeypatch.setattr(kb_retrieval_tools, "_embedding_provider", None)
    result = kb_retrieval_tools.search_knowledge_base("source", workspace_id="workspace-a")
    assert result[0]["chunk_id"] == "c1"
    with pytest.raises(GraphDBError):
        kb_retrieval_tools.search_knowledge_base("source", workspace_id="")


def test_bge_provider_uses_dense_normalized_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeModel:
        def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            assert texts == ["query"]
            assert kwargs["normalize_embeddings"] is True
            assert kwargs["convert_to_numpy"] is True
            return [[0.0, 0.6, 0.8]]

    monkeypatch.setattr(settings, "embedding_dimension", 3)
    provider = LocalBGEEmbeddingProvider(model=FakeModel())

    assert provider.embed_query("query") == [0.0, 0.6, 0.8]


def test_bge_provider_is_selected_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedding_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "bge_m3")

    assert isinstance(build_embedding_provider(), LocalBGEEmbeddingProvider)


def test_disabled_embeddings_do_not_create_pseudo_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedding_enabled", False)
    monkeypatch.setattr(settings, "embedding_provider", "none")

    assert build_embedding_provider() is None
