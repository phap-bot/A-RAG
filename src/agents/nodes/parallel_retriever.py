"""Parallel Retrieval Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 3: MCP Isolation. Must NOT import database drivers directly. Calls MCP tools from src.mcp_server.tools.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from src.agents.orchestrator.state import AgentState, RetrievedChunk
from src.core.config import logger, settings
from src.mcp_server.tools.kb_retrieval_tools import (
    query_knowledge_graph,
    search_knowledge_base,
)


def parallel_retriever_node(state: AgentState) -> Dict[str, Any]:
    """Execute parallel hybrid vector search and graph queries using MCP tools."""
    workspace_id = state.get("workspace_id", "")
    file_paths = state.get("file_paths", [])
    formulated_queries = state.get("formulated_queries", [state["query"]])
    graph_queries = state.get("graph_queries", [])

    logger.info(
        f"Entering Parallel Retriever: {len(formulated_queries)} vector queries, {len(graph_queries)} graph queries"
    )

    retrieved_chunks: List[RetrievedChunk] = []
    graph_results: List[Dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    errors: list[str] = []
    trace: dict[str, Any] = {"workspace_id": workspace_id, "vector_queries": [], "graph_queries": []}

    def search_one(query: str) -> tuple[str, list[dict[str, Any]]]:
        return query, search_knowledge_base(
            query=query,
            limit=settings.retrieval_top_k,
            filter_metadata={"file_paths": file_paths},
            workspace_id=workspace_id,
        )

    def graph_one(query: str) -> tuple[str, list[dict[str, Any]]]:
        return query, query_knowledge_graph(
            entity_query=query,
            max_hops=settings.neo4j_graph_max_hops,
            workspace_id=workspace_id,
        )

    # Both retrieval channels are submitted to one bounded pool. Each task
    # owns one MCP request; the driver/repository owns sessions and remains
    # safe for concurrent reads. This is parallel across vector and graph,
    # not merely parallel across queries inside one channel.
    with ThreadPoolExecutor(max_workers=settings.retrieval_parallel_workers) as executor:
        futures = {
            executor.submit(search_one, query): ("vector", query)
            for query in formulated_queries
        }
        futures.update({
            executor.submit(graph_one, query): ("graph", query)
            for query in graph_queries
        })
        for future in as_completed(futures):
            channel, requested_query = futures[future]
            try:
                query, result = future.result()
                if channel == "vector":
                    trace["vector_queries"].append({"query": query, "result_count": len(result)})
                    for raw in result:
                        chunk = RetrievedChunk(**raw)
                        if chunk.chunk_id not in seen_chunk_ids:
                            seen_chunk_ids.add(chunk.chunk_id)
                            retrieved_chunks.append(chunk)
                else:
                    trace["graph_queries"].append({"query": query, "result_count": len(result)})
                    graph_results.extend(result)
            except Exception as exc:
                message = f"{channel.title()} retrieval failed for '{requested_query}': {exc}"
                errors.append(message)
                logger.error(message)

    # Stable ordering makes downstream synthesis/replay deterministic even
    # though the upstream calls finish in an arbitrary order.
    retrieved_chunks.sort(key=lambda chunk: chunk.score, reverse=True)
    graph_results.sort(key=lambda relation: (
        str(relation.get("source_node", "")),
        str(relation.get("relationship", "")),
        str(relation.get("target_node", "")),
    ))
    trace["vector_queries"].sort(key=lambda item: item["query"])
    trace["graph_queries"].sort(key=lambda item: item["query"])
    errors.sort()

    logger.info(
        f"Parallel Retriever completed: {len(retrieved_chunks)} unique chunks, {len(graph_results)} graph relations"
    )

    return {
        "retrieved_docs": retrieved_chunks,
        "graph_context": graph_results,
        "retrieval_trace": trace,
        "errors": errors,
    }
