"""Unit tests for Step 1: Base Config, Pydantic Models, Exceptions, Prompts, and AgentState."""

import pytest
from pydantic import ValidationError

import src.core.config as config_module
from src.core.config import Settings, settings, setup_logger
from src.core.exceptions import (
    AppException,
    ConfigurationError,
    CriticReflectionError,
    IngestionError,
    MaxRetriesExceededError,
    MCPGatewayError,
    RetrievalError,
    VectorDBError,
)
from src.core.prompts import (
    CRITIC_REFLECTION_SYSTEM_PROMPT,
    CRITIC_REFLECTION_USER_TEMPLATE,
    QUERY_FORMULATION_SYSTEM_PROMPT,
    QUERY_FORMULATION_USER_TEMPLATE,
    SYNTHESIZER_SYSTEM_PROMPT,
    SYNTHESIZER_USER_TEMPLATE,
    VLM_IMAGE_CAPTIONING_SYSTEM_PROMPT,
    VLM_IMAGE_CAPTIONING_USER_TEMPLATE,
)
from src.agents.orchestrator.state import (
    AgentState,
    CriticVerdict,
    QueryFormulationOutput,
    RetrievedChunk,
    create_initial_agent_state,
)


def test_settings_defaults():
    """Verify that settings load with valid defaults."""
    cfg = Settings()
    assert cfg.app_name == "Enterprise-Agentic-RAG-BaaS"
    assert cfg.max_reflection_retries == 3
    assert cfg.vector_db_provider == "neo4j"
    assert cfg.log_format_json is True
    assert cfg.embedding_model_name == "BAAI/bge-m3"
    assert cfg.embedding_dimension == 1024
    assert cfg.embedding_provider == "bge_m3"


def test_setup_logger_normalizes_reconfigurable_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure Windows cp1252 stdout is normalized before Loguru adds sinks."""

    class FakeStdout:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.reconfigure_kwargs: dict[str, str] | None = None

        def reconfigure(self, **kwargs: str) -> None:
            self.reconfigure_kwargs = kwargs

    stdout = FakeStdout()
    monkeypatch.setattr(config_module.sys, "stdout", stdout)
    monkeypatch.setattr(config_module.logger, "remove", lambda: None)
    monkeypatch.setattr(config_module.logger, "add", lambda *args, **kwargs: None)

    setup_logger(Settings())

    assert stdout.reconfigure_kwargs == {
        "encoding": "utf-8",
        "errors": "backslashreplace",
    }


def test_custom_exceptions():
    """Verify exception hierarchy and JSON serializable dictionary generation."""
    err = VectorDBError("Connection timeout to Neo4j vector index", details={"host": "localhost", "port": 7687})
    assert isinstance(err, RetrievalError)
    assert isinstance(err, AppException)

    err_dict = err.to_dict()
    assert err_dict["error_type"] == "VectorDBError"
    assert "timeout" in err_dict["message"]
    assert err_dict["details"]["port"] == 7687

    retry_err = MaxRetriesExceededError("Reflection loop failed 3 times")
    assert isinstance(retry_err, CriticReflectionError)


def test_prompts_integrity():
    """Verify all system prompts and user templates are loaded and correctly formatted."""
    assert len(QUERY_FORMULATION_SYSTEM_PROMPT) > 50
    assert len(SYNTHESIZER_SYSTEM_PROMPT) > 50
    assert len(CRITIC_REFLECTION_SYSTEM_PROMPT) > 50
    assert len(VLM_IMAGE_CAPTIONING_SYSTEM_PROMPT) > 50

    # Ensure templates can be formatted without KeyError
    q_formatted = QUERY_FORMULATION_USER_TEMPLATE.format(
        query="What is the architecture?",
        history="[]",
        critic_feedback="None",
        retry_count=0,
        max_retries=3,
    )
    assert "What is the architecture?" in q_formatted

    s_formatted = SYNTHESIZER_USER_TEMPLATE.format(
        query="Test query",
        retrieved_context="Sample context",
        graph_context="[]",
    )
    assert "Test query" in s_formatted

    c_formatted = CRITIC_REFLECTION_USER_TEMPLATE.format(
        query="Test query",
        retrieved_context="Ctx",
        graph_context="Graph",
        candidate_response="Response",
    )
    assert "Candidate Synthesized Response" in c_formatted


def test_retrieved_chunk_model():
    """Test RetrievedChunk validation and serialization."""
    chunk = RetrievedChunk(
        chunk_id="chunk-001",
        content="Enterprise RAG architecture overview.",
        modality="text",
        source_type="hybrid",
        source_doc="architecture_spec.pdf",
        score=0.92,
        metadata={"page": 1, "section": "Introduction"},
    )
    assert chunk.chunk_id == "chunk-001"
    assert chunk.score == 0.92
    assert chunk.metadata["page"] == 1

    # Image modality with VLM caption
    img_chunk = RetrievedChunk(
        chunk_id="img-002",
        content="Workflow diagram showing 4 isolated zones",
        modality="image",
        source_type="vector_dense",
        source_doc="architecture_spec.pdf",
        vlm_caption="Diagram of Ingestion, Agentic, MCP, and Evaluation zones.",
    )
    assert img_chunk.modality == "image"
    assert img_chunk.vlm_caption is not None


def test_critic_verdict_model():
    """Test CriticVerdict scoring constraints and validation."""
    verdict = CriticVerdict(
        passed=True,
        faithfulness_score=0.95,
        relevance_score=0.90,
        citation_accuracy_score=1.0,
        critique_feedback="All facts faithfully grounded in context.",
        should_reformulate=False,
    )
    assert verdict.passed is True
    assert verdict.faithfulness_score == 0.95

    # Out of bounds score should raise ValidationError
    with pytest.raises(ValidationError):
        CriticVerdict(
            passed=False,
            faithfulness_score=1.5,  # Exceeds 1.0
            relevance_score=0.8,
            critique_feedback="Invalid score test",
        )


def test_query_formulation_output_model():
    """Test QueryFormulationOutput validation."""
    out = QueryFormulationOutput(
        original_query="Explain the MCP gateway",
        intent="architectural_explanation",
        hybrid_search_queries=["MCP gateway design", "Model Context Protocol tools"],
        graph_entity_queries=["MCPGateway", "KnowledgeBaaS"],
        reformulation_rationale="Initial retrieval attempt",
    )
    assert len(out.hybrid_search_queries) == 2
    assert len(out.graph_entity_queries) == 2


def test_agent_state_initialization():
    """Test initial AgentState creation."""
    state = create_initial_agent_state(query="How does self-reflection work?", max_retries=3)
    assert state["query"] == "How does self-reflection work?"
    assert state["retry_count"] == 0
    assert state["max_retries"] == 3
    assert state["retrieved_docs"] == []
    assert state["critique"] is None
    assert state["errors"] == []
