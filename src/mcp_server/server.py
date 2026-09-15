"""Network MCP gateway for the Neo4j-backed retrieval boundary.

The LangGraph retriever uses the same domain functions in-process to avoid a
needless loopback hop. This module exposes those functions over MCP for
external sub-agents and clients. It contains no Cypher and no routing policy;
the repository and its workspace-scoped tool contract remain the single
implementation point.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from src.core.config import settings
from src.mcp_server.tools.kb_retrieval_tools import (
    get_evidence,
    query_knowledge_graph,
    search_knowledge_base,
)


mcp = FastMCP(
    name=settings.mcp_server_name,
    instructions=(
        "Retrieve grounded knowledge from the workspace-scoped Neo4j graph. "
        "Every result preserves chunk identifiers and source lineage."
    ),
    host=settings.mcp_server_host,
    port=settings.mcp_server_port,
    log_level=settings.log_level.upper(),
    json_response=True,
    stateless_http=True,
)


@mcp.tool(name="search_knowledge_base")
def search_knowledge_base_mcp(
    query: str,
    workspace_id: str,
    limit: int | None = None,
    filter_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search indexed chunks with Neo4j full-text and optional dense vector fusion.

    ``workspace_id`` is mandatory for tenant isolation. Results include the
    original chunk text and provenance metadata; this tool never summarizes or
    rewrites source content.
    """
    return search_knowledge_base(
        query=query,
        limit=limit,
        filter_metadata=filter_metadata,
        workspace_id=workspace_id,
    )


@mcp.tool(name="query_knowledge_graph")
def query_knowledge_graph_mcp(
    entity_query: str,
    workspace_id: str,
    max_hops: int | None = None,
) -> list[dict[str, Any]]:
    """Expand bounded Neo4j provenance relationships in one workspace."""
    return query_knowledge_graph(
        entity_query=entity_query,
        max_hops=max_hops,
        workspace_id=workspace_id,
    )


@mcp.tool(name="get_evidence")
def get_evidence_mcp(workspace_id: str, chunk_id: str) -> dict[str, Any]:
    """Resolve one exact chunk by ID for citation verification and re-reading."""
    return get_evidence(workspace_id=workspace_id, chunk_id=chunk_id)


def main() -> None:
    """Run the MCP gateway using the configured streamable HTTP transport."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()


__all__ = ["main", "mcp"]
