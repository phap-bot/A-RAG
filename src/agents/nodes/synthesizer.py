"""Synthesizer Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 2: No hardcoded prompts. Imports templates from src.core.prompts.
"""

from typing import Any, Dict
from src.agents.orchestrator.state import AgentState
from src.core.config import logger


def synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """Run the tool-aware answer writer and let graph routing enforce review."""
    from src.agents.agent_runtime import run_agent_node

    logger.info("Entering Synthesizer agent")
    return run_agent_node("synthesizer", state)


def _fallback_synthesis(query: str, chunks: list[Any], graph: list[dict]) -> str:
    """Deterministic grounded response adhering to citation constraints."""
    if not chunks:
        return f"Based on available documentation, there is insufficient context to answer: '{query}'."

    primary_chunk = chunks[0]
    citations = f"[Chunk: {primary_chunk.chunk_id}]"

    response_lines = [
        f"Regarding your query **{query}**:",
        f"{primary_chunk.content} {citations}",
    ]

    if len(chunks) > 1 and chunks[1].modality == "table":
        response_lines.append(f"\nStructured Details:\n{chunks[1].content} [Chunk: {chunks[1].chunk_id}]")

    if graph:
        response_lines.append(
            f"\nRelated Architecture Entities: `{graph[0].get('source_node')}` -> `{graph[0].get('target_node')}` [Graph: {graph[0].get('relationship')}]."
        )

    return "\n\n".join(response_lines)
