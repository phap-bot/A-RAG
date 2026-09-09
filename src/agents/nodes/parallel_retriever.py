"""Parallel Retrieval Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 3: MCP Isolation. Must NOT import database drivers directly. Calls MCP tools from src.mcp_server.tools.
"""

from typing import Any, Dict, List
from src.agents.orchestrator.state import AgentState, RetrievedChunk
from src.core.config import logger
from src.mcp_server.tools.kb_retrieval_tools import (
    query_knowledge_graph,
    search_knowledge_base,
)


def parallel_retriever_node(state: AgentState) -> Dict[str, Any]:
    """Execute parallel hybrid vector search and graph queries using MCP tools."""
    formulated_queries = state.get("formulated_queries", [state["query"]])
    graph_queries = state.get("graph_queries", [])

    logger.info(
        f"Entering Parallel Retriever: {len(formulated_queries)} vector queries, {len(graph_queries)} graph queries"
    )

    retrieved_chunks: List[RetrievedChunk] = []
    seen_chunk_ids: set[str] = set()

    # 1. Parallel Hybrid Vector Retrieval via MCP Tool
    for q in formulated_queries:
        try:
            raw_chunks = search_knowledge_base(query=q, limit=5)
            for raw in raw_chunks:
                chunk = RetrievedChunk(**raw)
                if chunk.chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(chunk.chunk_id)
                    retrieved_chunks.append(chunk)
        except Exception as exc:
            logger.error(f"MCP search_knowledge_base failed for query '{q}': {exc}")

    # 2. Knowledge Graph Expansion via MCP Tool
    graph_results: List[Dict[str, Any]] = []
    for gq in graph_queries:
        try:
            triples = query_knowledge_graph(entity_query=gq, max_hops=2)
            graph_results.extend(triples)
        except Exception as exc:
            logger.error(f"MCP query_knowledge_graph failed for entity '{gq}': {exc}")

    logger.info(
        f"Parallel Retriever completed: {len(retrieved_chunks)} unique chunks, {len(graph_results)} graph relations"
    )

    return {
        "retrieved_docs": retrieved_chunks,
        "graph_context": graph_results,
    }
