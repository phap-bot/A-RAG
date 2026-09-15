"""Deterministic provenance gate for Zone 2 synthesized responses."""

from __future__ import annotations

import re
from typing import Any


_CHUNK_CITATION_RE = re.compile(r"\[Chunk:\s*([^\]]+)\]")
_GRAPH_CITATION_RE = re.compile(r"\[Graph:\s*([^\]]+)\]")


def validate_response_provenance(
    response: str,
    retrieved_docs: list[Any],
    graph_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check that response citations resolve to the current retrieval state.

    This gate validates references, not factual truth. The LLM critic still
    evaluates semantic faithfulness, while this deterministic layer prevents a
    model from citing an element/chunk that was never returned by retrieval.
    """
    chunk_ids = {str(chunk.chunk_id) for chunk in retrieved_docs}
    cited_chunk_ids = [match.strip() for match in _CHUNK_CITATION_RE.findall(response)]
    cited_graph_ids = [match.strip() for match in _GRAPH_CITATION_RE.findall(response)]
    graph_ids = {
        str(value)
        for relation in graph_context
        for value in (relation.get("source_node"), relation.get("target_node"), relation.get("relationship"))
        if value is not None
    }
    unknown_chunks = sorted({chunk_id for chunk_id in cited_chunk_ids if chunk_id not in chunk_ids})
    unknown_graph = sorted({graph_id for graph_id in cited_graph_ids if graph_id not in graph_ids})
    return {
        "valid": bool(response.strip()) and not unknown_chunks and not unknown_graph,
        "cited_chunk_ids": sorted(set(cited_chunk_ids)),
        "cited_graph_ids": sorted(set(cited_graph_ids)),
        "unknown_chunk_ids": unknown_chunks,
        "unknown_graph_ids": unknown_graph,
        "available_chunk_count": len(chunk_ids),
        "available_graph_relation_count": len(graph_context),
    }


__all__ = ["validate_response_provenance"]
