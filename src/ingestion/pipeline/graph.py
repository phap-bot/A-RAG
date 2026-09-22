"""Executable LangGraph for Zone 1 ingestion.

The graph owns the ingestion control flow.  Parsers, skills, chunking and
storage remain deterministic executors; they never decide which graph branch
to take.  This keeps the provenance contract intact while exposing real
LangGraph task/update/custom events to the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from src.core.config import settings
from src.core.exceptions import IngestionError
from src.ingestion.chunking import chunking_graph
from src.ingestion.chunking.state import ChunkingState
from src.ingestion.parser.models import (
    ContentChunk,
    ParsedDocument,
    ProfilerResult,
)
from src.ingestion.parser.profiler import DocumentProfiler
from src.ingestion.parser.skills import (
    _apply_skills_impl,
    mineru_parse_tool,
    parse_code_tool,
    parse_markdown_tool,
    parse_spreadsheet_tool,
    parse_text_tool,
)
from src.retrieval.embeddings import EmbeddingProvider
from src.retrieval.neo4j_repository import Neo4jRepository


@dataclass(frozen=True)
class IngestionPipelineRuntime:
    """Runtime dependencies injected into one ingestion run."""

    file_path: str
    workspace_id: str
    source_path: str
    repository: Neo4jRepository | None = None
    embedding_provider: EmbeddingProvider | None = None


class IngestionPipelineState(TypedDict, total=False):
    """Shared state exchanged by every ingestion node.

    ``document`` and ``chunks`` are the only content-bearing contracts.  No
    node is allowed to rebuild text from a chunk or replace a source element.
    ``runtime`` is process-local and is never serialized to the UI.
    """

    runtime: IngestionPipelineRuntime
    job_id: str
    document_id: str
    workspace_id: str
    source_path: str
    file_path: str
    profiler_result: ProfilerResult
    document: ParsedDocument
    chunks: list[ContentChunk]
    chunk_state: ChunkingState
    quality: dict[str, Any]
    ocr_attempts: int
    entities: list[dict[str, Any]]
    graph_relations: list[dict[str, Any]]
    embedding_result: dict[str, Any]
    graph_result: dict[str, Any]
    validation: dict[str, Any]
    pipeline_stage: str
    error: dict[str, Any]


def initial_ingestion_state(
    runtime: IngestionPipelineRuntime,
    *,
    job_id: str = "",
    document_id: str = "",
) -> IngestionPipelineState:
    """Create the immutable-input portion of a pipeline run state."""

    return IngestionPipelineState(
        runtime=runtime,
        job_id=job_id,
        document_id=document_id,
        workspace_id=runtime.workspace_id,
        source_path=runtime.source_path,
        file_path=runtime.file_path,
        ocr_attempts=0,
        pipeline_stage="uploaded",
    )


def _emit(payload: dict[str, Any]) -> None:
    """Emit a typed custom event when the graph is running in stream mode."""

    try:
        get_stream_writer()(payload)
    except RuntimeError:
        # Direct unit-test invocations do not always provide a stream writer.
        # The graph result remains authoritative in that mode.
        return


def _json_tool_result(raw: Any) -> ParsedDocument:
    """Validate a parser tool response back into the parser boundary model."""

    if isinstance(raw, ParsedDocument):
        return raw
    if hasattr(raw, "content"):
        raw = raw.content
    if isinstance(raw, str):
        raw = json.loads(raw)
    return ParsedDocument.model_validate(raw)


def _invoke_extraction_tool(state: IngestionPipelineState, config: RunnableConfig) -> ParsedDocument:
    """Dispatch one format parser tool using profiler evidence."""

    runtime = state["runtime"]
    profiler = state["profiler_result"]
    file_path = Path(runtime.file_path)
    extension = profiler.file_type.lower()

    _emit({
        "kind": "tool_started",
        "tool": "format_parser",
        "format": extension,
        "file_name": file_path.name,
    })
    try:
        if profiler.recommended_engine == "mineru":
            result = mineru_parse_tool.invoke(
                {"file_path": str(file_path), "backend": settings.mineru_backend},
                config=config,
            )
        elif extension in {"md", "markdown"}:
            result = parse_markdown_tool.invoke(
                {"markdown": file_path.read_text(encoding="utf-8"), "file_name": file_path.name},
                config=config,
            )
        elif extension in {"txt", "text", "log"}:
            result = parse_text_tool.invoke(
                {"text": file_path.read_text(encoding="utf-8"), "file_name": file_path.name},
                config=config,
            )
        elif extension in {"csv", "tsv"}:
            result = parse_spreadsheet_tool.invoke({"file_path": str(file_path)}, config=config)
        elif profiler.category.value == "code":
            result = parse_code_tool.invoke({"file_path": str(file_path)}, config=config)
        else:
            raise IngestionError(
                f"No extraction tool is configured for '.{extension}'",
                details={"file_type": extension, "recommended_engine": profiler.recommended_engine},
            )
        document = _json_tool_result(result)
        _emit({
            "kind": "tool_finished",
            "tool": "format_parser",
            "format": extension,
            "element_count": len(document.elements),
        })
        return document
    except Exception as exc:
        _emit({
            "kind": "tool_failed",
            "tool": "format_parser",
            "format": extension,
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        raise


def detect_type_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Classify the source before any parser is selected."""

    profiler = DocumentProfiler().profile(state["file_path"])
    _emit({
        "kind": "classification",
        "file_type": profiler.file_type,
        "category": profiler.category.value,
        "strategy": profiler.strategy.value,
        "recommended_engine": profiler.recommended_engine,
    })
    return {
        "profiler_result": profiler,
        "pipeline_stage": "classified",
    }


def extraction_node(
    state: IngestionPipelineState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Execute exactly one parser tool selected from profiler output."""

    document = _invoke_extraction_tool(state, config)
    return {"document": document, "pipeline_stage": "extracted"}


def quality_check_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Check whether extraction produced usable, provenance-bearing content."""

    document = state.get("document")
    usable_elements = [element for element in (document.elements if document else []) if element.content.strip()]
    good = bool(usable_elements)
    profiler = state["profiler_result"]
    ocr_supported = profiler.file_type in {"pdf", "png", "jpg", "jpeg", "webp", "docx", "pptx", "xlsx"}
    quality = {
        "status": "good" if good else "bad",
        "usable_element_count": len(usable_elements),
        "ocr_supported": ocr_supported,
    }
    _emit({"kind": "quality_check", **quality})
    return {"quality": quality, "pipeline_stage": "quality_checked"}


def _route_after_quality(state: IngestionPipelineState) -> str:
    """Route bad extraction to one bounded OCR retry, never an infinite loop."""

    quality = state.get("quality", {})
    if quality.get("status") == "good":
        decision = "normalize"
    elif quality.get("ocr_supported") and state.get("ocr_attempts", 0) < 1:
        decision = "ocr"
    else:
        decision = "fail"
    _emit({"kind": "route_selected", "router": "quality_check", "decision": decision})
    return decision


def quality_failure_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Fail closed when extraction is empty and OCR is not applicable."""

    raise IngestionError(
        "Extraction quality check failed and no OCR retry is available",
        details=state.get("quality", {}),
    )


def ocr_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Request one explicit OCR retry through the same configured MinerU pipeline."""

    attempts = int(state.get("ocr_attempts", 0)) + 1
    profiler = state["profiler_result"]
    if profiler.file_type not in {"pdf", "png", "jpg", "jpeg", "webp", "docx", "pptx", "xlsx"}:
        raise IngestionError(
            "OCR retry is only available for layout or image sources",
            details={"file_type": profiler.file_type},
        )
    _emit({"kind": "ocr_started", "attempt": attempts, "backend": settings.mineru_backend})
    return {"ocr_attempts": attempts, "pipeline_stage": "ocr_requested"}


def normalize_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Apply table/image skills without changing element identity or source text."""

    document = _apply_skills_impl(state["document"])
    return {"document": document, "pipeline_stage": "normalized"}


def entity_extraction_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Extract deterministic structural entities used by the graph lineage.

    This is intentionally not presented as semantic NER.  It materializes
    section/header entities from already validated chunk metadata. A future NER
    provider can add semantic entities without changing the provenance contract.
    """

    entities_by_key: dict[str, dict[str, Any]] = {}
    for chunk in state.get("chunks", []):
        for section in chunk.metadata.section_path:
            key = f"{chunk.metadata.document_id}:section:{hashlib.sha1(section.encode('utf-8')).hexdigest()[:12]}"
            entity = entities_by_key.setdefault(
                key,
                {"entity_id": key, "name": section, "type": "section", "chunk_ids": []},
            )
            if chunk.chunk_id not in entity["chunk_ids"]:
                entity["chunk_ids"].append(chunk.chunk_id)
    entities = list(entities_by_key.values())
    relations = [
        {"source": entity["entity_id"], "target": chunk_id, "relationship": "CONTAINS_CHUNK"}
        for entity in entities
        for chunk_id in entity["chunk_ids"]
    ]
    _emit({"kind": "entity_extraction", "mode": "structural_sections", "entity_count": len(entities)})
    return {
        "entities": entities,
        "graph_relations": relations,
        "pipeline_stage": "entities_extracted",
    }


def embedding_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Generate configured dense vectors while preserving each chunk identity."""

    provider = state["runtime"].embedding_provider
    chunks = list(state.get("chunks", []))
    if provider is None:
        return {
            "embedding_result": {"status": "skipped", "reason": "embedding_provider_disabled"},
        }
    vectors = provider.embed_documents([chunk.content for chunk in chunks])
    if len(vectors) != len(chunks):
        raise IngestionError(
            "Embedding provider returned an unexpected vector count",
            details={"chunks": len(chunks), "vectors": len(vectors)},
        )
    embedded = [chunk.model_copy(update={"embedding": vector}) for chunk, vector in zip(chunks, vectors)]
    _emit({"kind": "embedding_completed", "provider": settings.embedding_provider, "count": len(embedded)})
    return {
        "chunks": embedded,
        "embedding_result": {
            "status": "completed",
            "provider": settings.embedding_provider,
            "count": len(embedded),
            "dimension": len(vectors[0]) if vectors else settings.embedding_dimension,
        },
    }


def graph_extraction_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Build the Neo4j lineage payload from validated document/chunk relations."""

    relation_count = len(state.get("graph_relations", []))
    result = {
        "status": "prepared",
        "entity_count": len(state.get("entities", [])),
        "relation_count": relation_count,
    }
    _emit({"kind": "graph_extraction", **result})
    return {"graph_result": result}


def validate_index_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Gate indexing on chunk provenance and optional vector dimensions."""

    document = state["document"]
    chunks = list(state.get("chunks", []))
    errors: list[str] = []
    element_ids = {element.element_id for element in document.elements}
    for chunk in chunks:
        if chunk.metadata.document_id != document.document_id:
            errors.append(f"chunk {chunk.chunk_id} points to another document")
        if not chunk.metadata.element_ids:
            errors.append(f"chunk {chunk.chunk_id} has no source element IDs")
        if not set(chunk.metadata.element_ids).issubset(element_ids):
            errors.append(f"chunk {chunk.chunk_id} references unknown source elements")
        if settings.embedding_enabled and state["runtime"].embedding_provider is not None:
            if not chunk.embedding or len(chunk.embedding) != settings.embedding_dimension:
                errors.append(f"chunk {chunk.chunk_id} has no valid embedding")
    validation = {"status": "valid" if not errors else "invalid", "errors": errors}
    _emit({"kind": "index_validation", **validation})
    if errors:
        raise IngestionError("Ingestion index validation failed", details=validation)
    return {"validation": validation, "pipeline_stage": "validated"}


def neo4j_index_node(state: IngestionPipelineState) -> dict[str, Any]:
    """Persist the validated document, chunks, vectors and lineage in Neo4j."""

    repository = state["runtime"].repository
    if repository is None:
        result = {"status": "skipped", "reason": "neo4j_disabled"}
    else:
        result = repository.upsert_document(
            state["document"],
            workspace_id=state["workspace_id"],
            source_path=state["source_path"],
            chunks=state["chunks"],
            embedding_provider=None,
        )
        result = {"status": "completed", **result}
    _emit({"kind": "neo4j_index", **result})
    return {"graph_result": {**state.get("graph_result", {}), "index": result}, "pipeline_stage": "indexed"}


def build_ingestion_pipeline_graph():
    """Compile the full detect → extract → validate → index graph."""

    workflow = StateGraph(IngestionPipelineState)
    workflow.add_node("detect_type", detect_type_node)
    workflow.add_node("extraction", extraction_node)
    workflow.add_node("quality_check", quality_check_node)
    workflow.add_node("quality_failure", quality_failure_node)
    workflow.add_node("ocr", ocr_node)
    workflow.add_node("normalize", normalize_node)
    workflow.add_node("semantic_chunk", chunking_graph)
    workflow.add_node("entity_extraction", entity_extraction_node)
    workflow.add_node("embedding", embedding_node)
    workflow.add_node("graph_extraction", graph_extraction_node)
    workflow.add_node("validate", validate_index_node)
    workflow.add_node("neo4j_index", neo4j_index_node)

    workflow.add_edge(START, "detect_type")
    workflow.add_edge("detect_type", "extraction")
    workflow.add_edge("extraction", "quality_check")
    workflow.add_conditional_edges(
        "quality_check",
        _route_after_quality,
        {"ocr": "ocr", "normalize": "normalize", "fail": "quality_failure"},
    )
    workflow.add_edge("quality_failure", END)
    workflow.add_edge("ocr", "extraction")
    workflow.add_edge("normalize", "semantic_chunk")
    workflow.add_edge("semantic_chunk", "entity_extraction")
    workflow.add_edge("entity_extraction", "embedding")
    workflow.add_edge("entity_extraction", "graph_extraction")
    workflow.add_edge("embedding", "validate")
    workflow.add_edge("graph_extraction", "validate")
    workflow.add_edge("validate", "neo4j_index")
    workflow.add_edge("neo4j_index", END)
    return workflow.compile()


ingestion_pipeline_graph = build_ingestion_pipeline_graph()


def execute_ingestion_pipeline(
    state: IngestionPipelineState,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> IngestionPipelineState:
    """Run the graph and expose real LangGraph v2 stream parts to a sink."""

    final_state: IngestionPipelineState = dict(state)
    for part in ingestion_pipeline_graph.stream(
        state,
        stream_mode=["tasks", "updates", "custom", "values"],
        subgraphs=True,
        version="v2",
    ):
        if on_event is not None:
            on_event(part)
        if part.get("type") == "values" and isinstance(part.get("data"), dict):
            final_state = part["data"]
    return final_state


__all__ = [
    "IngestionPipelineRuntime",
    "IngestionPipelineState",
    "build_ingestion_pipeline_graph",
    "detect_type_node",
    "execute_ingestion_pipeline",
    "ingestion_pipeline_graph",
    "initial_ingestion_state",
]
