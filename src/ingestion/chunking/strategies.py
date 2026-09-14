"""Deterministic, provenance-preserving chunking strategies.

Chunking is deliberately deterministic. The agent may choose a plan, but it
must not rewrite source content or invent boundaries that cannot be traced back
to ``ParsedElement`` instances. Every draft produced here is later materialized
as a validated ``ContentChunk`` by the chunking orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Callable, Iterable, Sequence

from src.ingestion.parser.models import (
    ChunkStrategy,
    ChunkMetadata,
    ContentChunk,
    ModalityType,
    ParsedDocument,
    ParsedElement,
)
from src.ingestion.chunking.state import ChunkPlan, ChunkPlanGroup


CODE_EXTENSIONS = frozenset(
    {
        "py",
        "js",
        "ts",
        "java",
        "cpp",
        "c",
        "h",
        "go",
        "rs",
        "rb",
        "php",
        "sql",
        "sh",
        "bash",
        "yaml",
        "yml",
        "json",
        "xml",
        "html",
        "css",
        "toml",
        "ini",
        "cfg",
    }
)

TEXT_EXTENSIONS = frozenset({"md", "markdown", "txt", "text", "log"})
TABLE_EXTENSIONS = frozenset({"csv", "tsv"})
LAYOUT_EXTENSIONS = frozenset({"pdf", "docx", "pptx", "xlsx"})


def estimate_tokens(text: str) -> int:
    """Estimate tokens with the same conservative heuristic as ``ContentChunk``."""
    return max(1, math.ceil(len(text.split()) * 1.3))


def select_chunk_strategy(document: ParsedDocument) -> ChunkStrategy:
    """Select a safe deterministic strategy from the parsed document contract.

    Graph structure is represented as lineage metadata after chunking; it is
    not used to split raw content because graph edges alone do not preserve the
    text boundaries required by vector retrieval.
    """
    file_type = document.file_type.lower().lstrip(".")
    if file_type in TABLE_EXTENSIONS:
        return "row_window"
    if file_type in CODE_EXTENSIONS:
        return "code_boundary"
    if file_type in LAYOUT_EXTENSIONS:
        return "page_element"
    if file_type in TEXT_EXTENSIONS:
        has_hierarchy = any(element.metadata.section_path for element in document.elements)
        return "hierarchical_semantic" if has_hierarchy else "token_window"
    if any(element.metadata.element_type in {"image", "chart", "table"} for element in document.elements):
        return "page_element"
    return "token_window"


def _strategy_key(element: ParsedElement, strategy: ChunkStrategy) -> tuple[Any, ...]:
    """Return the source boundary key allowed for a strategy."""
    if strategy in {"row_window", "page_element"}:
        return (element.metadata.page_number, tuple(element.metadata.section_path))
    if strategy == "code_boundary":
        return (element.metadata.page_number, element.metadata.element_type)
    if strategy == "hierarchical_semantic":
        return (tuple(element.metadata.section_path),)
    return (element.metadata.page_number, tuple(element.metadata.section_path))


def build_deterministic_plan(
    document: ParsedDocument,
    strategy: ChunkStrategy | None = None,
) -> ChunkPlan:
    """Create a safe baseline plan grouped by contiguous source boundaries."""
    selected_strategy = strategy or select_chunk_strategy(document)
    groups: list[ChunkPlanGroup] = []
    current_ids: list[str] = []
    current_key: tuple[Any, ...] | None = None
    for element in document.elements:
        if not element.content.strip():
            continue
        key = _strategy_key(element, selected_strategy)
        if current_ids and key != current_key:
            groups.append(ChunkPlanGroup(element_ids=current_ids))
            current_ids = []
        current_key = key
        current_ids.append(element.element_id)
    if current_ids:
        groups.append(ChunkPlanGroup(element_ids=current_ids))
    return ChunkPlan(strategy=selected_strategy, groups=groups)


@dataclass
class _ChunkDraft:
    """Internal draft retaining all source elements used by one chunk."""

    body: str
    elements: list[ParsedElement]
    extra: dict[str, Any] = field(default_factory=dict)


def _context_prefix(elements: Sequence[ParsedElement]) -> str:
    """Build a compact embedding prefix from the full section breadcrumb."""
    if not elements:
        return ""
    section_path = elements[-1].metadata.section_path
    if not section_path:
        return ""
    return f"[Section: {' > '.join(section_path)}]\n"


def _joined_content(elements: Sequence[ParsedElement]) -> str:
    return "\n\n".join(element.content.strip() for element in elements if element.content.strip())


def _fits(elements: Sequence[ParsedElement], max_tokens: int) -> bool:
    return estimate_tokens(_context_prefix(elements) + _joined_content(elements)) <= max_tokens


def _is_atomic(element: ParsedElement) -> bool:
    return element.metadata.element_type in {"table", "image", "chart", "equation"}


def _split_text_element(
    element: ParsedElement,
    max_tokens: int,
    overlap_tokens: int,
    *,
    extra: dict[str, Any] | None = None,
) -> list[_ChunkDraft]:
    """Split one text/code element on sentence/line boundaries.

    ``overlap_tokens`` is applied as a small trailing word overlap only when a
    source element is larger than the target window. The original element ID
    stays on every segment, while segment offsets are recorded in metadata.
    """
    text = element.content.strip()
    if not text:
        return []
    if _fits([element], max_tokens):
        return [_ChunkDraft(body=text, elements=[element], extra=extra or {})]

    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if piece.strip()]
    if not pieces:
        pieces = [text]

    # First normalize the units so even one very long sentence can satisfy the
    # same budget as the regular sentence/line path. This avoids dropping text
    # or emitting an oversized atomic draft.
    units: list[tuple[str, bool]] = []
    context = _context_prefix([element])
    for piece in pieces:
        if estimate_tokens(context + piece) <= max_tokens:
            units.append((piece, False))
            continue
        words = piece.split()
        word_buffer: list[str] = []
        for word in words:
            candidate = " ".join([*word_buffer, word])
            if word_buffer and estimate_tokens(context + candidate) > max_tokens:
                units.append((" ".join(word_buffer), True))
                word_buffer = []
            if not word_buffer and estimate_tokens(context + word) > max_tokens:
                raise ValueError("max_tokens is too small for the section context")
            word_buffer.append(word)
        if word_buffer:
            units.append((" ".join(word_buffer), True))

    drafts: list[_ChunkDraft] = []
    current = ""
    current_start = 0
    current_word_split = False
    pending_overlap = ""

    def flush(end_index: int) -> None:
        nonlocal current, current_start, current_word_split, pending_overlap
        if not current:
            return
        body = current.strip()
        segment_extra = dict(extra or {})
        segment_extra.update(
            {
                "segment_index": len(drafts),
                "source_segment_start": current_start,
                "source_segment_end": end_index,
            }
        )
        if current_word_split:
            segment_extra["word_split"] = True
        drafts.append(_ChunkDraft(body=body, elements=[element], extra=segment_extra))
        pending_overlap = " ".join(body.split()[-max(0, overlap_tokens) :])
        current = ""
        current_word_split = False

    for index, (unit, word_split) in enumerate(units):
        overlap_prefix = pending_overlap
        pending_overlap = ""
        candidate = " ".join(part for part in (overlap_prefix, current, unit) if part).strip()
        if current and estimate_tokens(context + candidate) > max_tokens:
            flush(index - 1)
            overlap_prefix = pending_overlap
            pending_overlap = ""
            candidate = " ".join(part for part in (overlap_prefix, unit) if part).strip()
        # If overlap leaves no room for the next unit, discard only the
        # optional overlap; the source unit itself is never discarded.
        if estimate_tokens(context + candidate) > max_tokens:
            candidate = unit
            overlap_prefix = ""
        if not current:
            current_start = index
        current = candidate
        current_word_split = current_word_split or word_split

    flush(len(units) - 1)
    return drafts


def _split_table_element(element: ParsedElement, max_tokens: int) -> list[_ChunkDraft]:
    """Split an oversized Markdown table by rows and repeat its header."""
    lines = [line.strip() for line in element.content.splitlines() if line.strip()]
    if len(lines) <= 2:
        body = element.content.strip()
        if estimate_tokens(_context_prefix([element]) + body) > max_tokens:
            raise ValueError("Table header exceeds max_tokens; increase the chunk budget")
        return [_ChunkDraft(body=body, elements=[element])]

    header = lines[:2] if "---" in lines[1] else lines[:1]
    data_rows = lines[len(header) :]
    drafts: list[_ChunkDraft] = []
    current_rows: list[str] = []
    row_start = 0

    def flush(end_index: int) -> None:
        nonlocal current_rows, row_start
        if not current_rows:
            return
        body = "\n".join([*header, *current_rows])
        drafts.append(
            _ChunkDraft(
                body=body,
                elements=[element],
                extra={
                    "row_start": row_start,
                    "row_end": end_index,
                    "header_repeated": True,
                },
            )
        )
        current_rows = []
        row_start = end_index + 1

    context = _context_prefix([element])
    for index, row in enumerate(data_rows):
        candidate = "\n".join([*header, *current_rows, row])
        if current_rows and estimate_tokens(context + candidate) > max_tokens:
            flush(index - 1)
            candidate = "\n".join([*header, row])
        if estimate_tokens(context + candidate) <= max_tokens:
            current_rows.append(row)
            continue

        # A single pathological row can exceed the budget. Keep the repeated
        # header and split the row text into lossless fragments rather than
        # silently emitting an oversized vector payload.
        row_words = row.split()
        fragment: list[str] = []
        for word in row_words:
            fragment_candidate = "\n".join([*header, " ".join([*fragment, word])])
            if not fragment and estimate_tokens(context + fragment_candidate) > max_tokens:
                raise ValueError("Table header plus row data exceeds max_tokens")
            if fragment and estimate_tokens(context + fragment_candidate) > max_tokens:
                drafts.append(
                    _ChunkDraft(
                        body="\n".join([*header, " ".join(fragment)]),
                        elements=[element],
                        extra={
                            "row_start": index,
                            "row_end": index,
                            "header_repeated": True,
                            "row_fragment": True,
                        },
                    )
                )
                fragment = []
            fragment.append(word)
        if fragment:
            drafts.append(
                _ChunkDraft(
                    body="\n".join([*header, " ".join(fragment)]),
                    elements=[element],
                    extra={
                        "row_start": index,
                        "row_end": index,
                        "header_repeated": True,
                        "row_fragment": True,
                    },
                )
            )

    flush(len(data_rows) - 1)
    return drafts


def _split_code_element(element: ParsedElement, max_tokens: int) -> list[_ChunkDraft]:
    """Split an oversized code block on source lines while retaining formatting."""
    lines = element.content.splitlines() or [element.content]
    context = _context_prefix([element])
    drafts: list[_ChunkDraft] = []
    current: list[str] = []
    line_start = 0

    def flush(end_index: int) -> None:
        nonlocal current, line_start
        if not current:
            return
        drafts.append(
            _ChunkDraft(
                body="\n".join(current).strip(),
                elements=[element],
                extra={
                    "line_start": line_start,
                    "line_end": end_index,
                    "language": element.metadata.extra.get("language"),
                },
            )
        )
        current = []
        line_start = end_index + 1

    for index, line in enumerate(lines):
        candidate = "\n".join([*current, line]).strip()
        if current and estimate_tokens(context + candidate) > max_tokens:
            flush(index - 1)
            candidate = line.strip()
        if estimate_tokens(context + candidate) <= max_tokens:
            current.append(line)
            continue

        # A single source line can still be pathological. Split that line by
        # words as the final safety valve and mark the lossless text fragments.
        words = line.split()
        fragment: list[str] = []
        for word in words:
            word_candidate = " ".join([*fragment, word])
            if fragment and estimate_tokens(context + word_candidate) > max_tokens:
                drafts.append(
                    _ChunkDraft(
                        body=" ".join(fragment),
                        elements=[element],
                        extra={
                            "line_start": index,
                            "line_end": index,
                            "line_fragment": True,
                            "language": element.metadata.extra.get("language"),
                        },
                    )
                )
                fragment = []
            if not fragment and estimate_tokens(context + word) > max_tokens:
                raise ValueError("max_tokens is too small for the code section context")
            fragment.append(word)
        if fragment:
            drafts.append(
                _ChunkDraft(
                    body=" ".join(fragment),
                    elements=[element],
                    extra={
                        "line_start": index,
                        "line_end": index,
                        "line_fragment": True,
                        "language": element.metadata.extra.get("language"),
                    },
                )
            )

    flush(len(lines) - 1)
    return drafts


def _pack_elements(
    elements: Iterable[ParsedElement],
    max_tokens: int,
    strategy: ChunkStrategy,
    key_fn: Callable[[ParsedElement], tuple[Any, ...]],
    overlap_tokens: int,
) -> list[_ChunkDraft]:
    """Pack compatible text elements while keeping atomic elements intact."""
    drafts: list[_ChunkDraft] = []
    current: list[ParsedElement] = []
    current_key: tuple[Any, ...] | None = None

    def flush() -> None:
        nonlocal current, current_key
        if current:
            drafts.append(_ChunkDraft(body=_joined_content(current), elements=current.copy()))
        current = []
        current_key = None

    for element in elements:
        key = key_fn(element)
        if _is_atomic(element):
            flush()
            if element.metadata.element_type == "table":
                drafts.extend(_split_table_element(element, max_tokens))
            else:
                if not _fits([element], max_tokens):
                    raise ValueError(
                        f"Atomic {element.metadata.element_type} element exceeds max_tokens"
                    )
                drafts.append(_ChunkDraft(body=element.content.strip(), elements=[element]))
            continue

        if current and key != current_key:
            flush()
        current_key = key

        if not _fits([*current, element], max_tokens):
            flush()
        if _fits([element], max_tokens):
            current.append(element)
        else:
            flush()
            if strategy == "code_boundary" and element.metadata.element_type == "code":
                drafts.extend(_split_code_element(element, max_tokens))
            else:
                drafts.extend(
                    _split_text_element(
                        element,
                        max_tokens,
                        overlap_tokens,
                        extra={"packed_strategy": strategy},
                    )
                )
    flush()
    return drafts


def _build_drafts_for_elements(
    elements: Sequence[ParsedElement],
    strategy: ChunkStrategy,
    max_tokens: int,
    overlap_tokens: int,
) -> list[_ChunkDraft]:
    """Execute one already-approved source group deterministically."""
    if strategy == "row_window":
        return _pack_elements(
            elements,
            max_tokens,
            strategy,
            key_fn=lambda element: _strategy_key(element, strategy),
            overlap_tokens=overlap_tokens,
        )

    if strategy == "code_boundary":
        return _pack_elements(
            elements,
            max_tokens,
            strategy,
            key_fn=lambda element: _strategy_key(element, strategy),
            overlap_tokens=0,
        )

    if strategy == "page_element":
        return _pack_elements(
            elements,
            max_tokens,
            strategy,
            key_fn=lambda element: _strategy_key(element, strategy),
            overlap_tokens=0,
        )

    if strategy == "hierarchical_semantic":
        return _pack_elements(
            elements,
            max_tokens,
            strategy,
            key_fn=lambda element: _strategy_key(element, strategy),
            overlap_tokens=overlap_tokens,
        )

    return _pack_elements(
        elements,
        max_tokens,
        strategy,
        key_fn=lambda element: _strategy_key(element, strategy),
        overlap_tokens=overlap_tokens,
    )


def build_drafts(
    document: ParsedDocument,
    strategy: ChunkStrategy,
    max_tokens: int,
    overlap_tokens: int,
    plan_groups: Sequence[Sequence[str]] | None = None,
) -> list[_ChunkDraft]:
    """Execute approved groups into drafts without reading LLM-generated text.

    ``plan_groups`` contains only source element IDs. When omitted, the
    deterministic grouping policy is used. When supplied by the planner, IDs
    are resolved against ``document`` and validated before execution.
    """
    if max_tokens < 16:
        raise ValueError("max_tokens must be at least 16 to preserve section context")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be >= 0 and smaller than max_tokens")

    non_empty = [element for element in document.elements if element.content.strip()]
    if plan_groups is None:
        plan = build_deterministic_plan(document, strategy)
        plan_groups = [group.element_ids for group in plan.groups]

    lookup = {element.element_id: element for element in non_empty}
    flattened_ids = [element_id for group in plan_groups for element_id in group]
    if len(flattened_ids) != len(set(flattened_ids)):
        raise ValueError("Chunk plan contains duplicate element IDs")
    unknown_ids = sorted(set(flattened_ids) - set(lookup))
    missing_ids = sorted(set(lookup) - set(flattened_ids))
    if unknown_ids:
        raise ValueError(f"Chunk plan contains unknown element IDs: {unknown_ids}")
    if missing_ids:
        raise ValueError(f"Chunk plan omits source element IDs: {missing_ids}")
    if flattened_ids != [element.element_id for element in non_empty]:
        raise ValueError("Chunk plan must preserve source element order")

    drafts: list[_ChunkDraft] = []
    for group_index, group in enumerate(plan_groups):
        elements = [lookup[element_id] for element_id in group]
        if not elements:
            continue
        group_drafts = _build_drafts_for_elements(
            elements,
            strategy,
            max_tokens,
            overlap_tokens,
        )
        for draft in group_drafts:
            draft.extra = {"plan_group_index": group_index, **draft.extra}
        drafts.extend(group_drafts)
    return drafts


def _resolve_modality(elements: Sequence[ParsedElement]) -> ModalityType:
    types = {element.metadata.element_type for element in elements}
    if types == {"table"}:
        return "table"
    if types == {"image"}:
        return "image"
    if types == {"chart"}:
        return "chart"
    if types == {"code"}:
        return "code"
    return "text"


def materialize_chunks(
    document: ParsedDocument,
    drafts: Sequence[_ChunkDraft],
    strategy: ChunkStrategy,
) -> list[ContentChunk]:
    """Convert drafts to deterministic ``ContentChunk`` models and link parents."""
    chunks: list[ContentChunk] = []
    for draft in drafts:
        if not draft.body.strip():
            continue
        index = len(chunks)
        elements = draft.elements
        modality = _resolve_modality(elements)
        section_path = list(elements[-1].metadata.section_path) if elements else []
        page_numbers = sorted({element.metadata.page_number for element in elements}) or [1]
        element_ids = list(dict.fromkeys(element.element_id for element in elements))
        element_types = list(dict.fromkeys(element.metadata.element_type for element in elements))
        language = next(
            (
                element.metadata.extra.get("language")
                for element in elements
                if element.metadata.extra.get("language")
            ),
            None,
        )
        body = draft.body.strip()
        content = _context_prefix(elements) + body
        chunk_id = f"{document.document_id}-chunk-{index:04d}"
        metadata = ChunkMetadata(
            document_id=document.document_id,
            source_doc=document.file_name,
            page_numbers=page_numbers,
            element_ids=element_ids,
            modality=modality,
            section_path=section_path,
            parent_header=elements[-1].metadata.parent_header if elements else None,
            has_table="table" in element_types,
            has_image=bool({"image", "chart"} & set(element_types)),
            language=language,
            chunk_strategy=strategy,
            chunk_index=index,
            extra={
                "source_element_indices": [element.metadata.element_index for element in elements],
                "element_types": element_types,
                "element_section_paths": [list(element.metadata.section_path) for element in elements],
                "source_file_type": document.file_type,
                "estimated_tokens": estimate_tokens(content),
                **draft.extra,
            },
        )
        table_markdown = body if modality == "table" else None
        code_snippet = body if modality == "code" else None
        captions = [element.vlm_caption for element in elements if element.vlm_caption]
        chunks.append(
            ContentChunk(
                chunk_id=chunk_id,
                content=content,
                metadata=metadata,
                table_markdown=table_markdown,
                vlm_caption="\n".join(captions) if captions else None,
                code_snippet=code_snippet,
            )
        )

    header_chunks: dict[tuple[str, ...], str] = {}
    for chunk in chunks:
        if "header" in chunk.metadata.extra.get("element_types", []):
            header_chunks.setdefault(tuple(chunk.metadata.section_path), chunk.chunk_id)

    for chunk in chunks:
        path = tuple(chunk.metadata.section_path)
        element_types = set(chunk.metadata.extra.get("element_types", []))
        candidate_paths = [path]
        if "header" in element_types and path:
            candidate_paths = [path[:-1], *candidate_paths]
        # A chunk containing its own header is the root of that section, not
        # its own parent. Body-only chunks may inherit the section's header.
        if "header" in element_types:
            candidate_paths = [path[:-1]] if path else []
        parent_id = next(
            (
                header_chunks[candidate]
                for candidate in candidate_paths
                if candidate in header_chunks and header_chunks[candidate] != chunk.chunk_id
            ),
            None,
        )
        chunk.metadata.parent_chunk_id = parent_id

    return chunks


def build_element_chunk_map(chunks: Sequence[ContentChunk]) -> dict[str, list[str]]:
    """Index every source element to the chunks containing it."""
    mapping: dict[str, list[str]] = {}
    for chunk in chunks:
        for element_id in chunk.metadata.element_ids:
            mapping.setdefault(element_id, []).append(chunk.chunk_id)
    return mapping


def build_section_chunk_map(chunks: Sequence[ContentChunk]) -> dict[str, list[str]]:
    """Index section breadcrumbs to chunk IDs for hierarchical retrieval."""
    mapping: dict[str, list[str]] = {}
    for chunk in chunks:
        section_key = " > ".join(chunk.metadata.section_path) or "__root__"
        mapping.setdefault(section_key, []).append(chunk.chunk_id)
    return mapping


__all__ = [
    "build_drafts",
    "build_deterministic_plan",
    "build_element_chunk_map",
    "build_section_chunk_map",
    "estimate_tokens",
    "materialize_chunks",
    "select_chunk_strategy",
]
