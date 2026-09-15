"""AgentState and Core Data Contracts for LangGraph Multi-Agent Orchestration.

RULE 1: Shared State ONLY. Agents must ONLY read from and write to AgentState.
RULE 5: Strict Typing with PEP 484 and Pydantic v2 validation.
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict
from pydantic import BaseModel, Field


ModalityType = Literal["text", "table", "image", "chart", "code"]
SourceType = Literal["vector_dense", "bm25", "neo4j_graph", "hybrid"]


class RetrievedChunk(BaseModel):
    """Normalized retrieved knowledge chunk from Vector DB or Knowledge Graph."""

    chunk_id: str = Field(..., description="Unique chunk identifier")
    content: str = Field(..., description="Text content or extracted Markdown table/image description")
    modality: ModalityType = Field(default="text", description="Data modality of the chunk")
    source_type: SourceType = Field(default="hybrid", description="Retrieval source channel")
    source_doc: str = Field(..., description="Source document name or URI")
    score: float = Field(default=0.0, ge=0.0, description="Relevance or similarity score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary chunk metadata (page, section, etc.)")
    vlm_caption: Optional[str] = Field(default=None, description="Detailed VLM description if modality is image/chart")

    # Optional top-level projections of ContentChunk lineage. Storage adapters
    # may keep the same values nested in ``metadata``; these fields make the
    # contract directly usable by Zone 2 when a backend returns them flattened.
    document_id: Optional[str] = Field(default=None, description="Parent document ID")
    page_numbers: List[int] = Field(default_factory=list, description="Source pages")
    element_ids: List[str] = Field(default_factory=list, description="Source ParsedElement IDs")
    section_path: List[str] = Field(default_factory=list, description="Heading breadcrumb")
    parent_chunk_id: Optional[str] = Field(default=None, description="Nearest heading/parent chunk")


class CriticVerdict(BaseModel):
    """Structured critique output from the Self-Reflection loop."""

    passed: bool = Field(..., description="Whether the synthesized response passes quality thresholds")
    faithfulness_score: float = Field(
        ..., ge=0.0, le=1.0, description="Faithfulness / hallucination-free score (0 to 1)"
    )
    relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance to user question score (0 to 1)"
    )
    citation_accuracy_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Accuracy of cited chunk citations"
    )
    critique_feedback: str = Field(
        ..., description="Specific feedback on gaps, inaccuracies, or hallucinations"
    )
    should_reformulate: bool = Field(
        default=False, description="Whether to trigger query formulation retry"
    )
    suggested_query_refinements: List[str] = Field(
        default_factory=list, description="Targeted query suggestions for next retrieval attempt"
    )


class QueryFormulationOutput(BaseModel):
    """Output from the Query Formulation agent node."""

    original_query: str = Field(..., description="Initial raw user query")
    intent: str = Field(..., description="Identified user intent")
    hybrid_search_queries: List[str] = Field(
        default_factory=list, description="Targeted queries for hybrid vector/dense/BM25 retrieval"
    )
    graph_entity_queries: List[str] = Field(
        default_factory=list, description="Targeted entity names/types for Neo4j Cypher querying"
    )
    reformulation_rationale: Optional[str] = Field(
        default=None, description="Reasoning behind query decomposition or reflection retry adjustments"
    )


class AgentState(TypedDict):
    """Central Shared State for the LangGraph Multi-Agent Orchestrator.
    
    All nodes must read from and write to this state dictionary exclusively.
    """

    query: str
    workspace_id: str
    file_paths: List[str]
    history: List[Dict[str, str]]
    formulated_queries: List[str]
    graph_queries: List[str]
    retrieved_docs: List[RetrievedChunk]
    graph_context: List[Dict[str, Any]]
    synthesized_response: Optional[str]
    critique: Optional[CriticVerdict]
    retry_count: int
    max_retries: int
    retrieval_trace: Dict[str, Any]
    provenance_validation: Dict[str, Any]
    errors: List[str]


def create_initial_agent_state(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    max_retries: int = 3,
    workspace_id: str = "",
    file_paths: Optional[List[str]] = None,
) -> AgentState:
    """Helper to initialize a clean AgentState container."""
    return AgentState(
        query=query,
        workspace_id=workspace_id,
        file_paths=file_paths or [],
        history=history or [],
        formulated_queries=[query],
        graph_queries=[],
        retrieved_docs=[],
        graph_context=[],
        synthesized_response=None,
        critique=None,
        retry_count=0,
        max_retries=max_retries,
        retrieval_trace={},
        provenance_validation={},
        errors=[],
    )


__all__ = [
    "ModalityType",
    "SourceType",
    "RetrievedChunk",
    "CriticVerdict",
    "QueryFormulationOutput",
    "AgentState",
    "create_initial_agent_state",
]
