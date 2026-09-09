"""Query Formulation Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 2: No hardcoded prompts. Imports templates from src.core.prompts.
"""

from typing import Any, Dict
import json
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.orchestrator.state import AgentState, QueryFormulationOutput
from src.core.config import logger, settings
from src.core.llm_client import get_chat_llm
from src.core.prompts import (
    QUERY_FORMULATION_SYSTEM_PROMPT,
    QUERY_FORMULATION_USER_TEMPLATE,
)


def query_formulator_node(state: AgentState) -> Dict[str, Any]:
    """Analyze query and critique feedback, formulating targeted hybrid and graph queries."""
    query = state["query"]
    history = state.get("history", [])
    critique = state.get("critique")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    logger.info(
        f"Entering Query Formulator: query='{query}', retry={retry_count}/{max_retries}"
    )

    critic_feedback_str = (
        critique.critique_feedback if critique else "None (Initial attempt)"
    )
    if critique and critique.suggested_query_refinements:
        critic_feedback_str += f" | Suggestions: {critique.suggested_query_refinements}"

    formatted_user_prompt = QUERY_FORMULATION_USER_TEMPLATE.format(
        query=query,
        history=str(history),
        critic_feedback=critic_feedback_str,
        retry_count=retry_count,
        max_retries=max_retries,
    )

    # Use LLM or deterministic fallback if no API key is provided
    if settings.openai_api_key and settings.openai_api_key != "sk-mock-key-replace-with-actual":
        try:
            llm = get_chat_llm(temperature=0.0)
            messages = [
                SystemMessage(content=QUERY_FORMULATION_SYSTEM_PROMPT),
                HumanMessage(content=formatted_user_prompt),
            ]
            response = llm.invoke(messages)
            parsed_json = json.loads(response.content)
            output = QueryFormulationOutput(**parsed_json)
            formulated = output.hybrid_search_queries or [query]
            graph_queries = output.graph_entity_queries or []
        except Exception as exc:
            logger.warning(f"LLM query formulation failed: {exc}. Using deterministic formulation.")
            formulated, graph_queries = _fallback_formulation(query, critique)
    else:
        formulated, graph_queries = _fallback_formulation(query, critique)

    logger.info(
        f"Query Formulator output: {len(formulated)} hybrid queries, {len(graph_queries)} graph queries"
    )

    return {
        "formulated_queries": formulated,
        "graph_queries": graph_queries,
    }


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
