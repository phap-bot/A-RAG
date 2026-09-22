"""Retrieval tools used by Zone 2 and exposed by the MCP gateway.

These functions contain no routing heuristics. They validate workspace scope
and delegate to the configured Neo4j repository, or to the explicitly injected
local workspace adapter used by the single-process API development mode.
"""

from __future__ import annotations

from typing import Any, Callable
from threading import Lock

from src.core.config import logger, settings
from src.core.exceptions import ConfigurationError, GraphDBError
from src.retrieval.embeddings import EmbeddingProvider, build_embedding_provider
from src.retrieval.neo4j_repository import Neo4jRepository


_repository: Neo4jRepository | None = None
_embedding_provider: EmbeddingProvider | None = None
_local_search: Callable[..., list[dict[str, Any]]] | None = None
_local_graph: Callable[..., list[dict[str, Any]]] | None = None
_local_evidence: Callable[..., dict[str, Any] | None] | None = None
_repository_lock = Lock()


def configure_retrieval(
    *,
    repository: Neo4jRepository | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    local_search: Callable[..., list[dict[str, Any]]] | None = None,
    local_graph: Callable[..., list[dict[str, Any]]] | None = None,
    local_evidence: Callable[..., dict[str, Any] | None] | None = None,
) -> None:
    """Inject either the Neo4j backend or the local service adapter."""
    global _repository, _embedding_provider, _local_search, _local_graph, _local_evidence
    _repository = repository
    _embedding_provider = embedding_provider
    _local_search = local_search
    _local_graph = local_graph
    _local_evidence = local_evidence


def _get_repository() -> Neo4jRepository:
    global _repository
    if not settings.neo4j_enabled and _repository is None:
        raise ConfigurationError(
            "Neo4j retrieval is disabled",
            details={"set": "NEO4J_ENABLED=true", "provider": settings.vector_db_provider},
        )
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = Neo4jRepository.from_settings()
                _repository.prepare()
    return _repository


def _get_embedding_provider() -> EmbeddingProvider | None:
    global _embedding_provider
    if _embedding_provider is None and settings.embedding_enabled:
        _embedding_provider = build_embedding_provider()
    return _embedding_provider


def _require_workspace(workspace_id: str) -> str:
    if not workspace_id.strip():
        raise GraphDBError(
            "workspace_id is required for retrieval isolation",
            details={"policy": "no_cross_workspace_search"},
        )
    return workspace_id


def _file_paths(filter_metadata: dict[str, Any] | None) -> list[str]:
    if not filter_metadata:
        return []
    paths = filter_metadata.get("file_paths", [])
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("filter_metadata.file_paths must be a list of strings")
    return paths


def search_knowledge_base(
    query: str,
    limit: int | None = None,
    filter_metadata: dict[str, Any] | None = None,
    *,
    workspace_id: str = "",
) -> list[dict[str, Any]]:
    """Search Neo4j Chunk nodes using vector and full-text retrieval.

    The workspace is mandatory even though it is keyword-only. When
    embeddings are enabled, the query is embedded and fused with Neo4j
    full-text ranks using repository-level reciprocal-rank fusion. When
    embeddings are disabled, the lexical channel remains available with
    ``source_type='bm25'`` and no fake vector score is produced.
    """
    workspace = _require_workspace(workspace_id)
    if _local_search is not None:
        return _local_search(
            query=query,
            limit=limit or settings.retrieval_top_k,
            filter_metadata=filter_metadata,
            workspace_id=workspace,
        )
    repository = _get_repository()
    provider = _get_embedding_provider()
    query_embedding = provider.embed_query(query) if provider else None
    requested = limit or settings.retrieval_top_k
    logger.info(
        f"Executing Neo4j retrieval: workspace={workspace} limit={requested} "
        f"vector={'enabled' if query_embedding else 'disabled'}"
    )
    return repository.search_hybrid(
        workspace_id=workspace,
        keyword_query=query,
        query_embedding=query_embedding,
        top_k=requested,
        file_paths=_file_paths(filter_metadata),
    )


def query_knowledge_graph(
    entity_query: str,
    max_hops: int | None = None,
    *,
    workspace_id: str = "",
    file_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Expand Neo4j provenance/entity relationships within one workspace."""
    workspace = _require_workspace(workspace_id)
    if _local_graph is not None:
        return _local_graph(
            entity_query=entity_query,
            max_hops=max_hops or settings.neo4j_graph_max_hops,
            workspace_id=workspace,
            file_paths=file_paths,
        )
    return _get_repository().query_graph(
        workspace_id=workspace,
        entity_query=entity_query,
        max_hops=max_hops or settings.neo4j_graph_max_hops,
        limit=settings.retrieval_top_k,
        file_paths=file_paths,
    )


def get_evidence(*, workspace_id: str, chunk_id: str) -> dict[str, Any]:
    """Resolve one exact Chunk node for a citation/evidence request."""
    workspace = _require_workspace(workspace_id)
    if _local_evidence is not None:
        result = _local_evidence(workspace_id=workspace, chunk_id=chunk_id)
        if result is None:
            raise GraphDBError(
                "Evidence chunk was not found in the local workspace store",
                details={"workspace_id": workspace, "chunk_id": chunk_id},
            )
        return result
    result = _get_repository().get_chunk(workspace_id=workspace, chunk_id=chunk_id)
    if result is None:
        raise GraphDBError(
            "Evidence chunk was not found in Neo4j",
            details={"workspace_id": workspace_id, "chunk_id": chunk_id},
        )
    return result


__all__ = [
    "configure_retrieval",
    "get_evidence",
    "query_knowledge_graph",
    "search_knowledge_base",
]
