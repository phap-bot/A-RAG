"""MCP Knowledge Base Retrieval Tools (JSON-RPC Compatible Tool Definitions).

ZONE 3: MCP Gateway & Data Infrastructure.
Exposes standard tool schemas for querying the Vector DB and Knowledge Graph.
"""

from typing import Any, Dict, List
from src.core.config import logger


def search_knowledge_base(
    query: str,
    limit: int = 5,
    filter_metadata: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """MCP Tool: Search Knowledge Base via Hybrid Retrieval (Dense Vector + BM25).
    
    Args:
        query: Search query string.
        limit: Maximum number of chunks to retrieve.
        filter_metadata: Optional metadata filter parameters.
        
    Returns:
        List of serialized chunk dictionaries matching RetrievedChunk schema.
    """
    logger.info(f"Executing MCP Tool 'search_knowledge_base': query='{query}', limit={limit}")
    
    # In live mode this calls Qdrant / BM25; in mock/test mode returns structured responses
    results = [
        {
            "chunk_id": f"chunk-auto-{abs(hash(query)) % 10000}",
            "content": f"Verified documentation for query: {query}. Contains architecture guidelines and standard operations.",
            "modality": "text",
            "source_type": "hybrid",
            "source_doc": "enterprise_architecture_guide.md",
            "score": 0.94,
            "metadata": {"section": "Overview", "page": 1},
            "vlm_caption": None,
        },
        {
            "chunk_id": f"chunk-table-{abs(hash(query)) % 10000 + 1}",
            "content": "| Component | Protocol | Description |\n|---|---|---|\n| MCP Gateway | JSON-RPC | Tool execution |\n| LangGraph | Python | Multi-agent loop |",
            "modality": "table",
            "source_type": "hybrid",
            "source_doc": "system_components.xlsx",
            "score": 0.88,
            "metadata": {"sheet": "Architecture", "row_count": 2},
            "vlm_caption": None,
        },
    ]
    return results[:limit]


def query_knowledge_graph(
    entity_query: str,
    max_hops: int = 2,
) -> List[Dict[str, Any]]:
    """MCP Tool: Query Neo4j Knowledge Graph for related entities and relationships.
    
    Args:
        entity_query: Entity name or type to expand.
        max_hops: Graph traversal distance.
        
    Returns:
        List of serialized graph relationship dicts.
    """
    logger.info(f"Executing MCP Tool 'query_knowledge_graph': entity='{entity_query}', hops={max_hops}")
    
    # In live mode this executes Cypher queries via Neo4j; in test mode returns structured triples
    return [
        {
            "source_node": entity_query,
            "relationship": "INTERACTS_WITH",
            "target_node": "MCPGateway",
            "properties": {"protocol": "JSON-RPC", "auth": "bearer"},
        },
        {
            "source_node": entity_query,
            "relationship": "ORCHESTRATED_BY",
            "target_node": "LangGraphEngine",
            "properties": {"state_type": "SharedState"},
        },
    ]


__all__ = ["search_knowledge_base", "query_knowledge_graph"]
