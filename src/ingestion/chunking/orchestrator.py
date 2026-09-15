"""LangGraph orchestration for provenance-safe document chunking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from src.core.config import logger
from src.ingestion.chunking.state import (
    ChunkPlan,
    ChunkingState,
    create_initial_chunking_state,
)
from src.ingestion.chunking.strategies import (
    build_drafts,
    build_deterministic_plan,
    build_element_chunk_map,
    build_section_chunk_map,
    estimate_tokens,
    materialize_chunks,
    select_chunk_strategy,
)
from src.ingestion.parser.models import ContentChunk, ParsedDocument


_SKILL_ROOT = Path(__file__).resolve().parents[3] / ".vibeflow" / "skills"


def load_skill(skill_name: str) -> str:
    """Load one approved chunking skill from the repository at runtime."""
    if skill_name != "chunking-orchestrator":
        raise ValueError(f"Unsupported chunking skill: {skill_name}")

    skill_path = _SKILL_ROOT / skill_name / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"Chunking skill not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def _error_update(state: ChunkingState, error: Exception) -> ChunkingState:
    """Return a graph-compatible structured error state."""
    message = f"{type(error).__name__}: {error}"
    logger.exception(f"Chunking graph failed: {error}")
    return ChunkingState(
        stage="error",
        errors=[*state.get("errors", []), message],
        validation={"valid": False, "error": message},
    )


def _document_inventory(document: ParsedDocument) -> list[dict[str, Any]]:
    """Expose metadata only to the planner; never expose source content."""
    return [
        {
            "element_id": element.element_id,
            "element_index": element.metadata.element_index,
            "element_type": element.metadata.element_type,
            "page_number": element.metadata.page_number,
            "section_path": element.metadata.section_path,
            "parent_header": element.metadata.parent_header,
            "header_level": element.metadata.header_level,
            "language": element.metadata.extra.get("language"),
            "content_present": bool(element.content.strip()),
        }
        for element in document.elements
    ]


def _allowed_strategies(document: ParsedDocument) -> set[str]:
    """Return strategies the planner may select for this source shape."""
    recommended = select_chunk_strategy(document)
    file_type = document.file_type.lower().lstrip(".")
    if file_type in {"md", "markdown", "txt", "text", "log"}:
        return {recommended, "token_window"}
    return {recommended}


def _validate_plan(document: ParsedDocument, plan: ChunkPlan) -> ChunkPlan:
    """Validate planner references before the executor can read any content."""
    non_empty = [element for element in document.elements if element.content.strip()]
    source_ids = [element.element_id for element in non_empty]
    lookup = {element.element_id: element for element in non_empty}
    flattened_ids = [element_id for group in plan.groups for element_id in group.element_ids]

    if plan.strategy not in _allowed_strategies(document):
        raise ValueError(
            f"Planner strategy '{plan.strategy}' is not allowed for '.{document.file_type}'"
        )
    if len(flattened_ids) != len(set(flattened_ids)):
        raise ValueError("Planner returned duplicate element IDs")
    unknown_ids = sorted(set(flattened_ids) - set(lookup))
    if unknown_ids:
        raise ValueError(f"Planner returned unknown element IDs: {unknown_ids}")
    if flattened_ids != source_ids:
        raise ValueError("Planner must cover every non-empty element in source order")

    for group in plan.groups:
        elements = [lookup[element_id] for element_id in group.element_ids]
        section_paths = {tuple(element.metadata.section_path) for element in elements}
        page_numbers = {element.metadata.page_number for element in elements}
        if len(section_paths) > 1:
            raise ValueError("Planner group mixes unrelated section paths")
        if plan.strategy in {"page_element", "code_boundary", "row_window"} and len(page_numbers) > 1:
            raise ValueError(f"Planner group mixes pages for strategy '{plan.strategy}'")
    return plan


def _planner_messages(document: ParsedDocument, skill_content: str) -> list[Any]:
    """Build the planner prompt with a strict no-content/no-rewrite role."""
    system = SystemMessage(
        content=(
            "You are the Chunk Planning Agent. You may choose a strategy and "
            "group existing source element IDs only. Never output source text, "
            "summaries, rewritten content, invented IDs, or metadata values "
            "that are not present in the inventory. Return only JSON matching "
            "ChunkPlan: {strategy, groups:[{element_ids:[...]}]}. The executor "
            "will read content directly from ParsedDocument. Preserve source "
            "order and never mix unrelated section paths.\n\n"
            "===== RUNTIME CHUNKING SKILL =====\n"
            f"{skill_content}\n"
            "===== END RUNTIME CHUNKING SKILL ====="
        )
    )
    inventory = json.dumps(_document_inventory(document), ensure_ascii=False)
    user = HumanMessage(
        content=(
            "Create a ChunkPlan for this document. Use only the supplied IDs. "
            "Do not include a content field.\n"
            f"file_type: {document.file_type}\n"
            f"total_pages: {document.total_pages}\n"
            f"element_inventory: {inventory}"
        )
    )
    return [system, user]


def _parse_plan_response(value: Any) -> ChunkPlan:
    """Convert structured or JSON model output into the strict plan contract."""
    if isinstance(value, ChunkPlan):
        return value
    if isinstance(value, dict):
        return ChunkPlan.model_validate(value)

    content = getattr(value, "content", value)
    if isinstance(content, dict):
        return ChunkPlan.model_validate(content)
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content).strip()
    candidates = [text]
    if text.startswith("```"):
        lines = text.splitlines()
        candidates.append("\n".join(lines[1:-1]).strip())
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in dict.fromkeys(item for item in candidates if item):
        try:
            return ChunkPlan.model_validate(json.loads(candidate))
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError("Planner response is not valid ChunkPlan JSON")


def _invoke_planner(planner_llm: BaseChatModel, messages: Sequence[Any]) -> ChunkPlan:
    """Prefer provider-enforced structured output, with JSON parsing fallback."""
    structured_output = getattr(planner_llm, "with_structured_output", None)
    if callable(structured_output):
        try:
            response = structured_output(ChunkPlan).invoke(list(messages))
            return _parse_plan_response(response)
        except (NotImplementedError, TypeError, ValueError):
            # Some local/fake models do not implement provider-side schemas;
            # the strict Pydantic parse below remains the final gate.
            pass
    return _parse_plan_response(planner_llm.invoke(list(messages)))


def plan_chunking_node(
    state: ChunkingState,
    planner_llm: BaseChatModel | None = None,
) -> ChunkingState:
    """Load skill and obtain a constrained plan, with safe deterministic fallback."""
    try:
        document = state["document"]
        skill_name = state.get("skill_name", "chunking-orchestrator")
        skill_content = load_skill(skill_name)
        recommended_strategy = select_chunk_strategy(document)
        planner_mode = "deterministic"
        planner_warnings = list(state.get("planner_warnings", []))
        if planner_llm is None:
            plan = build_deterministic_plan(document, recommended_strategy)
        else:
            try:
                plan = _validate_plan(
                    document,
                    _invoke_planner(planner_llm, _planner_messages(document, skill_content)),
                )
                planner_mode = "llm"
            except Exception as planner_error:
                plan = build_deterministic_plan(document, recommended_strategy)
                planner_mode = "deterministic_fallback"
                planner_warnings.append(
                    f"Planner rejected; deterministic plan used: {type(planner_error).__name__}: {planner_error}"
                )
        return ChunkingState(
            skill_name=skill_name,
            skill_content=skill_content,
            strategy=plan.strategy,
            plan=plan,
            planner_mode=planner_mode,
            planner_warnings=planner_warnings,
            stage="planned",
            validation={
                "valid": True,
                "strategy": plan.strategy,
                "planner_mode": planner_mode,
                "source_file_type": document.file_type,
            },
        )
    except Exception as error:
        return _error_update(state, error)


def build_chunks_node(state: ChunkingState) -> ChunkingState:
    """Build typed chunks while preserving source order and hierarchy."""
    try:
        document = state["document"]
        strategy = state.get("strategy")
        if strategy is None:
            raise ValueError("Chunking strategy is missing; run plan_strategy first")

        plan = state.get("plan")
        plan_groups = [group.element_ids for group in plan.groups] if plan else None

        drafts = build_drafts(
            document,
            strategy,
            state.get("max_tokens", 400),
            state.get("overlap_tokens", 40),
            plan_groups=plan_groups,
        )
        chunks = materialize_chunks(document, drafts, strategy)
        return ChunkingState(
            chunks=chunks,
            element_chunk_map=build_element_chunk_map(chunks),
            section_chunk_map=build_section_chunk_map(chunks),
            stage="chunked",
        )
    except Exception as error:
        return _error_update(state, error)


def validate_chunks_node(state: ChunkingState) -> ChunkingState:
    """Validate token bounds, source coverage, and reconstructable lineage."""
    try:
        document = state["document"]
        chunks = [ContentChunk.model_validate(chunk) for chunk in state.get("chunks", [])]
        expected_elements = {
            element.element_id for element in document.elements if element.content.strip()
        }
        if len(expected_elements) != len(
            [element.element_id for element in document.elements if element.content.strip()]
        ):
            raise ValueError("ParsedDocument contains duplicate non-empty element IDs")
        actual_elements = set(state.get("element_chunk_map", {}))
        missing_elements = sorted(expected_elements - actual_elements)
        duplicate_chunk_ids = len({chunk.chunk_id for chunk in chunks}) != len(chunks)
        max_observed_tokens = max((estimate_tokens(chunk.content) for chunk in chunks), default=0)
        max_tokens = state.get("max_tokens", 400)
        oversized_chunks = [
            chunk.chunk_id
            for chunk in chunks
            if estimate_tokens(chunk.content) > max_tokens
        ]
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        invalid_parents = [
            chunk.chunk_id
            for chunk in chunks
            if chunk.metadata.parent_chunk_id
            and (
                chunk.metadata.parent_chunk_id not in chunk_ids
                or chunk.metadata.parent_chunk_id == chunk.chunk_id
            )
        ]
        metadata_errors: list[str] = []
        for index, chunk in enumerate(chunks):
            if chunk.metadata.document_id != document.document_id:
                metadata_errors.append(f"{chunk.chunk_id}: document_id mismatch")
            if chunk.metadata.source_doc != document.file_name:
                metadata_errors.append(f"{chunk.chunk_id}: source_doc mismatch")
            if chunk.metadata.chunk_index != index:
                metadata_errors.append(f"{chunk.chunk_id}: chunk_index is not contiguous")
            if any(page < 1 or page > document.total_pages for page in chunk.metadata.page_numbers):
                metadata_errors.append(f"{chunk.chunk_id}: page_numbers out of range")
            section_paths = {
                tuple(path)
                for path in chunk.metadata.extra.get("element_section_paths", [])
            }
            if len(section_paths) > 1:
                metadata_errors.append(f"{chunk.chunk_id}: unrelated section paths mixed")
        expected_element_map = build_element_chunk_map(chunks)
        expected_section_map = build_section_chunk_map(chunks)

        if missing_elements:
            raise ValueError(f"Source elements are not covered by chunks: {missing_elements}")
        if duplicate_chunk_ids:
            raise ValueError("Chunk IDs must be unique within a document")
        if oversized_chunks:
            raise ValueError(
                f"Chunks exceed max_tokens={max_tokens}: {oversized_chunks}"
            )
        if invalid_parents:
            raise ValueError(f"Chunks contain invalid parent_chunk_id values: {invalid_parents}")
        if metadata_errors:
            raise ValueError("Chunk metadata validation failed: " + "; ".join(metadata_errors))
        if state.get("element_chunk_map", {}) != expected_element_map:
            raise ValueError("element_chunk_map does not match materialized chunks")
        if state.get("section_chunk_map", {}) != expected_section_map:
            raise ValueError("section_chunk_map does not match materialized chunks")
        if expected_elements and not chunks:
            raise ValueError("Non-empty document produced no chunks")

        validation = {
            "valid": True,
            "strategy": state.get("strategy"),
            "chunk_count": len(chunks),
            "source_element_count": len(expected_elements),
            "covered_element_count": len(actual_elements & expected_elements),
            "coverage_complete": not missing_elements,
            "max_observed_tokens": max_observed_tokens,
            "max_tokens": max_tokens,
            "section_count": len(state.get("section_chunk_map", {})),
            "planner_mode": state.get("planner_mode", "deterministic"),
            "planner_warnings": state.get("planner_warnings", []),
        }
        return ChunkingState(chunks=chunks, stage="validated", validation=validation)
    except Exception as error:
        return _error_update(state, error)


def _route_after_plan(state: ChunkingState) -> str:
    """Stop on a planning error instead of cascading into build failures."""
    return "end" if state.get("stage") == "error" else "continue"


def _route_after_build(state: ChunkingState) -> str:
    """Stop on a materialization error instead of validating partial output."""
    return "end" if state.get("stage") == "error" else "continue"


def build_chunking_graph(planner_llm: BaseChatModel | None = None):
    """Compile the plan → build → validate StateGraph."""
    workflow = StateGraph(ChunkingState)

    def run_planner(state: ChunkingState) -> ChunkingState:
        return plan_chunking_node(state, planner_llm=planner_llm)

    workflow.add_node("plan_strategy", run_planner)
    workflow.add_node("build_chunks", build_chunks_node)
    workflow.add_node("validate_chunks", validate_chunks_node)
    workflow.add_edge(START, "plan_strategy")
    workflow.add_conditional_edges(
        "plan_strategy",
        _route_after_plan,
        {"continue": "build_chunks", "end": END},
    )
    workflow.add_conditional_edges(
        "build_chunks",
        _route_after_build,
        {"continue": "validate_chunks", "end": END},
    )
    workflow.add_edge("validate_chunks", END)
    return workflow.compile()


chunking_graph = build_chunking_graph()


def run_chunking(
    document: ParsedDocument,
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 40,
    planner_llm: BaseChatModel | None = None,
) -> ChunkingState:
    """Run chunk planning, materialization, and validation for one document."""
    initial_state = create_initial_chunking_state(
        document,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
    )
    graph = build_chunking_graph(planner_llm) if planner_llm is not None else chunking_graph
    return graph.invoke(initial_state)


def chunk_document(
    document: ParsedDocument,
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 40,
    planner_llm: BaseChatModel | None = None,
) -> list[ContentChunk]:
    """Return validated chunks or raise with the graph's structured errors."""
    result = run_chunking(
        document,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        planner_llm=planner_llm,
    )
    if result.get("stage") != "validated":
        errors = "; ".join(result.get("errors", [])) or "Unknown chunking error"
        raise ValueError(errors)
    return result.get("chunks", [])


__all__ = [
    "build_chunking_graph",
    "build_chunks_node",
    "ChunkPlan",
    "chunk_document",
    "chunking_graph",
    "load_skill",
    "plan_chunking_node",
    "run_chunking",
    "validate_chunks_node",
]
