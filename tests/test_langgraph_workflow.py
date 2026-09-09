"""Unit and integration tests for LangGraph Agentic Orchestrator and Reflection Loop."""

import pytest
from src.agents.orchestrator.graph import agentic_rag_app, build_agentic_rag_graph
from src.agents.orchestrator.state import create_initial_agent_state


def test_graph_nodes_registered():
    """Verify that all four sub-agent nodes are correctly wired in the LangGraph."""
    graph = build_agentic_rag_graph()
    nodes = graph.nodes
    assert "query_formulator" in nodes
    assert "parallel_retriever" in nodes
    assert "synthesizer" in nodes
    assert "critic_reflection" in nodes


def test_langgraph_happy_path_execution():
    """Verify full end-to-end execution of the LangGraph multi-agent loop."""
    initial_state = create_initial_agent_state(
        query="What is the architecture of Knowledge BaaS?",
        max_retries=3,
    )

    final_state = agentic_rag_app.invoke(initial_state)

    # 1. State integrity
    assert final_state is not None
    assert final_state["query"] == "What is the architecture of Knowledge BaaS?"
    assert len(final_state["formulated_queries"]) >= 1

    # 2. Parallel retriever results populated via MCP tools
    assert len(final_state["retrieved_docs"]) > 0
    assert len(final_state["graph_context"]) > 0

    # 3. Synthesizer output
    synthesized = final_state["synthesized_response"]
    assert synthesized is not None
    assert len(synthesized) > 30
    assert "[Chunk:" in synthesized

    # 4. Critic reflection passed
    critique = final_state["critique"]
    assert critique is not None
    assert critique.passed is True
    assert critique.faithfulness_score >= 0.85
    assert final_state["retry_count"] >= 1


def test_langgraph_reflection_loop_retry():
    """Verify that the reflection conditional edge loops back on critique failure."""
    # When query contains 'simulate_retry', the mock synthesis and critic will trigger 1 retry
    initial_state = create_initial_agent_state(
        query="simulate_retry for MCP architecture",
        max_retries=3,
    )

    final_state = agentic_rag_app.invoke(initial_state)

    # The reflection loop must have executed at least 2 iterations
    assert final_state["retry_count"] >= 2
    assert final_state["synthesized_response"] is not None
