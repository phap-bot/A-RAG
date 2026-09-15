"""Shared state contract for the deterministic chunking graph."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from src.ingestion.parser.models import ChunkStrategy, ContentChunk, ParsedDocument


ChunkingStage = Literal["raw", "planned", "chunked", "validated", "error"]
PlannerMode = Literal["deterministic", "llm", "deterministic_fallback"]


class ChunkPlanGroup(BaseModel):
    """A planner-approved group of source element IDs.

    Deliberately contains no source text. The executor resolves these IDs back
    to the immutable ``ParsedDocument`` before creating any chunk content.
    """

    model_config = ConfigDict(extra="forbid")

    element_ids: List[str] = Field(..., min_length=1)


class ChunkPlan(BaseModel):
    """Strict planner output; source content is forbidden by the contract."""

    model_config = ConfigDict(extra="forbid")

    strategy: ChunkStrategy
    groups: List[ChunkPlanGroup] = Field(default_factory=list)


class ChunkingState(TypedDict, total=False):
    """State exchanged by the chunking planner, builder, and validator.

    The graph accepts a validated ``ParsedDocument`` and emits only
    ``ContentChunk`` models plus explicit reverse indexes.  Keeping indexes in
    state makes the document-to-chunk relationship available to the embedding
    and storage stages without relying on vector-store insertion order.
    """

    document: ParsedDocument
    skill_name: str
    skill_content: str
    strategy: Optional[ChunkStrategy]
    plan: Optional[ChunkPlan]
    planner_mode: PlannerMode
    planner_warnings: List[str]
    max_tokens: int
    overlap_tokens: int
    chunks: List[ContentChunk]
    element_chunk_map: Dict[str, List[str]]
    section_chunk_map: Dict[str, List[str]]
    validation: Dict[str, Any]
    stage: ChunkingStage
    errors: List[str]


def create_initial_chunking_state(
    document: ParsedDocument,
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 40,
) -> ChunkingState:
    """Create a clean chunking state from one parsed document.

    Validation of the actual document contract is intentionally delegated to
    Pydantic before this function is called.  Only chunking controls are
    checked here so invalid budgets fail before the graph starts mutating
    derived state.
    """
    if max_tokens < 16:
        raise ValueError("max_tokens must be at least 16")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be >= 0 and smaller than max_tokens")

    return ChunkingState(
        document=document,
        skill_name="chunking-orchestrator",
        skill_content="",
        strategy=None,
        plan=None,
        planner_mode="deterministic",
        planner_warnings=[],
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        chunks=[],
        element_chunk_map={},
        section_chunk_map={},
        validation={},
        stage="raw",
        errors=[],
    )


__all__ = [
    "ChunkingStage",
    "ChunkPlanGroup",
    "ChunkPlan",
    "PlannerMode",
    "ChunkingState",
    "create_initial_chunking_state",
]
