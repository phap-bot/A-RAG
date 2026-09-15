"""Unit and integration tests for LangGraph Agentic Orchestrator and Reflection Loop."""

import pytest
from src.agents.orchestrator.graph import agentic_rag_app, build_agentic_rag_graph
from src.agents.orchestrator.state import create_initial_agent_state


@pytest.fixture()
def neo4j_retrieval_stub(monkeypatch):
    """Keep graph tests deterministic without hiding production Neo4j failures."""
    def search_knowledge_base(query, limit=None, filter_metadata=None, *, workspace_id=""):
        return [
            {
                "chunk_id": f"test-chunk-{abs(hash(query)) % 10000}",
                "content": f"Verified test context for query: {query}.",
                "modality": "text",
                "source_type": "hybrid",
                "source_doc": "test-architecture.md",
                "score": 0.94,
                "metadata": {"workspace_id": workspace_id, "section_path": ["Test"]},
                "document_id": "test-document",
                "section_path": ["Test"],
            }
        ][: limit or 5]

    def query_knowledge_graph(entity_query, max_hops=2, *, workspace_id=""):
        return [{
            "source_node": entity_query,
            "relationship": "TEST_RELATION",
            "target_node": "TestArchitecture",
            "properties": {"workspace_id": workspace_id, "max_hops": max_hops},
        }]

    monkeypatch.setattr(
        "src.agents.nodes.parallel_retriever.search_knowledge_base",
        search_knowledge_base,
    )
    monkeypatch.setattr(
        "src.agents.nodes.parallel_retriever.query_knowledge_graph",
        query_knowledge_graph,
    )


def test_graph_nodes_registered():
    """Verify that all four sub-agent nodes are correctly wired in the LangGraph."""
    graph = build_agentic_rag_graph()
    nodes = graph.nodes
    assert "query_formulator" in nodes
    assert "parallel_retriever" in nodes
    assert "synthesizer" in nodes
    assert "critic_reflection" in nodes


def test_langgraph_happy_path_execution(neo4j_retrieval_stub):
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


def test_langgraph_reflection_loop_retry(neo4j_retrieval_stub):
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
