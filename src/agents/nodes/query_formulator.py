"""Query Formulation Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 2: No hardcoded prompts. Imports templates from src.core.prompts.
"""

from typing import Any, Dict

from src.agents.orchestrator.state import AgentState, QueryFormulationOutput
from src.core.config import logger


def query_formulator_node(state: AgentState) -> Dict[str, Any]:
    """Run the Formulator as a tool-aware team member, not a direct search caller."""
    from src.agents.agent_runtime import run_agent_node

    logger.info("Entering Query Formulator agent")
    return run_agent_node("query_formulator", state)


def _fallback_formulation(query: str, critique: Any) -> tuple[list[str], list[str]]:
    """Deterministic formulation when running in local/test/mock mode."""
    hybrid_queries = [query]
    words = [w.strip(",.?!") for w in query.split() if len(w) > 3]
    if words:
        hybrid_queries.append(" ".join(words[:4]))
    if critique and hasattr(critique, "suggested_query_refinements") and critique.suggested_query_refinements:
        hybrid_queries.extend(critique.suggested_query_refinements)

    graph_entities = [w for w in words if w[0].isupper()] if words else ["KnowledgeBase"]
    if not graph_entities:
        graph_entities = ["EnterpriseRAG"]

    return hybrid_queries, graph_entities
