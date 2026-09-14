"""Shared state contract for the agentic ingestion parsing graph.

The graph uses one state object as the hand-off contract between profiler,
layout/skill tools, and the final JSON conversion step. Each stage adds its
validated output without discarding the previous representation.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages

from src.ingestion.parser.models import ParsedDocument, ProfilerResult


ParsingStage = Literal["raw", "markdown", "json", "error"]
PipelineName = Literal[
    "mineru",
    "pdf",
    "markdown",
    "text",
    "spreadsheet",
    "image",
    "code",
    "unsupported",
]


class AgentState(TypedDict, total=False):
    """State shared by all nodes in the ingestion LangGraph.

    ``raw_content`` is the source representation, ``markdown_content`` is the
    intermediate extraction representation, and ``parsed_document`` is the
    validated Pydantic JSON contract consumed by downstream pipeline stages.
    The earlier values remain available for provenance and retry decisions.
    """

    file_path: Optional[str]
    raw_content: str
    profiler_result: Optional[ProfilerResult]
    pipeline: Optional[PipelineName]
    markdown_content: str
    parsed_document: Optional[ParsedDocument]
    output_json: Optional[Dict[str, Any]]
    output_path: Optional[str]
    stage: ParsingStage
    errors: List[str]
    # ``add_messages`` is required by LangGraph's ToolNode.  It appends the
    # AI tool-call message and the resulting ToolMessage instead of replacing
    # the conversation on every graph transition.
    messages: Annotated[List[Any], add_messages]
    skill_name: Optional[str]
    llm_iterations: int
    max_llm_iterations: int


def create_initial_state(
    raw_content: str = "",
    file_path: Optional[str] = None,
) -> AgentState:
    """Create a clean graph state at the raw-input stage.

    Args:
        raw_content: Optional source text available before graph execution.
        file_path: Optional source path used by file-oriented tools.

    Returns:
        An ``AgentState`` initialized for the ``raw`` stage with no derived
        markdown, JSON document, profiler result, or errors.
    """
    return AgentState(
        file_path=file_path,
        raw_content=raw_content,
        profiler_result=None,
        pipeline=None,
        markdown_content="",
        parsed_document=None,
        output_json=None,
        output_path=None,
        stage="raw",
        errors=[],
        messages=[],
        skill_name="ingestion-orchestrator",
        llm_iterations=0,
        max_llm_iterations=8,
    )


__all__ = ["ParsingStage", "PipelineName", "AgentState", "create_initial_state"]
