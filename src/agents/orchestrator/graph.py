"""LangGraph Multi-Agent Orchestrator StateGraph.

Coordinates Query Formulation, Parallel Retrieval, Synthesis, and Critic Reflection loop.
RULE 1: Shared State ONLY. Reads and writes to AgentState.
"""

from langgraph.graph import END, START, StateGraph

from src.agents.nodes.critic_reflection import (
    critic_reflection_node,
    should_continue_reflection,
)
from src.agents.nodes.parallel_retriever import parallel_retriever_node
from src.agents.nodes.query_formulator import query_formulator_node
from src.agents.nodes.synthesizer import synthesizer_node
from src.agents.orchestrator.state import AgentState
from src.core.config import logger


def build_agentic_rag_graph():
    """Construct and compile the LangGraph multi-agent execution pipeline."""
    logger.info("Assembling Agentic RAG StateGraph...")

    workflow = StateGraph(AgentState)

    # 1. Register Sub-Agent Nodes
    workflow.add_node("query_formulator", query_formulator_node)
    workflow.add_node("parallel_retriever", parallel_retriever_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("critic_reflection", critic_reflection_node)

    # 2. Add Fixed Directed Edges
    workflow.add_edge(START, "query_formulator")
    workflow.add_edge("query_formulator", "parallel_retriever")
    workflow.add_edge("parallel_retriever", "synthesizer")
    workflow.add_edge("synthesizer", "critic_reflection")

    # 3. Add Reflection Conditional Edge
    workflow.add_conditional_edges(
        "critic_reflection",
        should_continue_reflection,
        {
            "reformulate": "query_formulator",
            "end": END,
        },
    )

    compiled_graph = workflow.compile()
    logger.info("Agentic RAG StateGraph assembled and compiled successfully.")
    return compiled_graph


# Singleton compiled graph instance
agentic_rag_app = build_agentic_rag_graph()

__all__ = ["build_agentic_rag_graph", "agentic_rag_app"]
