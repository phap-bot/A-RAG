"""Main-agent LangGraph with specialist handoffs and a shared tool executor."""

from langgraph.graph import END, START, StateGraph

from src.agents.nodes.critic_reflection import critic_reflection_node
from src.agents.nodes.parallel_retriever import parallel_retriever_node
from src.agents.nodes.query_formulator import query_formulator_node
from src.agents.nodes.synthesizer import synthesizer_node
from src.agents.agent_runtime import (
    execute_agent_tools,
    route_after_agent,
    route_after_tools,
    run_agent_node,
)
from src.agents.orchestrator.state import AgentState
from src.core.config import logger


def build_agentic_rag_graph():
    """Build the supervisor/team workflow shown in the runtime flow UI.

    Main chooses a specialist or a knowledge tool. Each specialist can call
    its role-scoped tools through the shared ToolNode and return findings to
    Main. A candidate answer cannot bypass Critic review.
    """
    logger.info("Assembling Main-agent Agentic RAG StateGraph...")

    workflow = StateGraph(AgentState)

    # Main is the supervisor; specialists are collaborative workers.
    workflow.add_node("agent_main", lambda state: run_agent_node("agent_main", state))
    workflow.add_node("query_formulator", query_formulator_node)
    workflow.add_node("parallel_retriever", parallel_retriever_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("critic_reflection", critic_reflection_node)
    workflow.add_node("tools", execute_agent_tools)

    workflow.add_edge(START, "agent_main")

    # Every role either continues its own tool loop or returns control to
    # Main. Main's conditional route may hand off to any specialist and is
    # guarded by the mandatory Critic/best-candidate terminal rules.
    main_routes = {
        "tools": "tools",
        "query_formulator": "query_formulator",
        "parallel_retriever": "parallel_retriever",
        "synthesizer": "synthesizer",
        "critic_reflection": "critic_reflection",
        "end": END,
    }
    workflow.add_conditional_edges(
        "agent_main",
        lambda state: route_after_agent("agent_main", state),
        main_routes,
    )
    for role in (
        "query_formulator",
        "parallel_retriever",
        "synthesizer",
        "critic_reflection",
    ):
        workflow.add_conditional_edges(
            role,
            lambda state, current=role: route_after_agent(current, state),
            {"tools": "tools", "agent_main": "agent_main"},
        )
    workflow.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "agent_main": "agent_main",
            "query_formulator": "query_formulator",
            "parallel_retriever": "parallel_retriever",
            "synthesizer": "synthesizer",
            "critic_reflection": "critic_reflection",
        },
    )

    compiled_graph = workflow.compile()
    logger.info("Main-agent Agentic RAG StateGraph assembled and compiled successfully.")
    return compiled_graph


# Singleton compiled graph instance
agentic_rag_app = build_agentic_rag_graph()

__all__ = ["build_agentic_rag_graph", "agentic_rag_app"]
