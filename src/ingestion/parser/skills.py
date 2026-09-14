"""Focused extraction skills for the Zone 1 ingestion parser.

ZONE 1: Ingestion Pipeline (Offline).
Directory Lock: src/ingestion/parser/

Skills operate on validated Pydantic models (ParsedElement, ParsedDocument) and
return the same strict contract. Raw strings/Markdown never leave parser/.

Available Skills:
1. TableParsingSkill: Normalizes Markdown table cells and preserves column/row metadata.
2. SpreadsheetParsingSkill: Parses CSV/TSV data into structured Markdown table elements.
3. VLMCaptioningSkill: Enriches image/chart elements using VLM prompts or deterministic fallback.
4. MinerUAdapter: HTTP boundary for PDF, Office, and image extraction.
"""

from __future__ import annotations

from collections.abc import Callable
import csv
import io
import json
import os
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.core.config import logger, settings
from src.core.llm_client import get_vlm_llm
from src.core.prompts import (
    VLM_IMAGE_CAPTIONING_SYSTEM_PROMPT,
    VLM_IMAGE_CAPTIONING_USER_TEMPLATE,
)
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.mineru_adapter import MinerUAdapter
from src.ingestion.parser.models import (
    ElementMetadata,
    ParsedDocument,
    ParsedElement,
    generate_doc_id,
    generate_element_id,
)
from src.ingestion.parser.profiler import DocumentProfiler


# =====================================================================
# 1. TABLE PARSING SKILLS
# =====================================================================

class TableParsingSkill:
    """Normalize Markdown table cells while preserving provenance metadata."""

    def apply(self, element: ParsedElement) -> ParsedElement:
        """Normalize a validated table element in place and return it."""
        if element.metadata.element_type != "table":
            raise ValueError("TableParsingSkill requires a table element")

        rows = [self._normalize_row(line) for line in element.content.splitlines() if line.strip()]
        if not rows:
            raise ValueError("Table element must contain at least one row")

        element.content = "\n".join(rows)
        if element.raw_content is None:
            element.raw_content = element.content

        headers = self._cells(rows[0])
        element.metadata.extra.update(
            {
                "row_count": len(rows),
                "col_count": len(headers),
                "headers": headers,
                "normalized": True,
            }
        )
        return element

    @staticmethod
    def _cells(row: str) -> list[str]:
        parts = row.strip().strip("|").split("|")
        return [part.strip() for part in parts]

    @classmethod
    def _normalize_row(cls, row: str) -> str:
        cells = cls._cells(row)
        return "| " + " | ".join(cells) + " |"


class SpreadsheetParsingSkill:
    """Specialized skill to parse CSV, TSV, or spreadsheet content into table elements."""

    def parse_csv(
        self,
        csv_text: str,
        source_doc: str = "data.csv",
        delimiter: str = ",",
        doc_id: Optional[str] = None,
        element_index: int = 0,
    ) -> ParsedElement:
        """Parse raw CSV string into a structured ParsedElement table."""
        doc_id = doc_id or generate_doc_id(csv_text.encode("utf-8"))
        f = io.StringIO(csv_text.strip())
        reader = csv.reader(f, delimiter=delimiter)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

        if not rows:
            raise ValueError("CSV content contains no non-empty rows")

        headers = [c.strip() for c in rows[0]]
        md_lines = ["| " + " | ".join(headers) + " |"]
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for row in rows[1:]:
            cells = [c.strip() for c in row]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            else:
                cells = cells[:len(headers)]
            md_lines.append("| " + " | ".join(cells) + " |")

        table_content = "\n".join(md_lines)
        return ParsedElement(
            element_id=generate_element_id(doc_id, element_index),
            content=table_content,
            raw_content=csv_text,
            metadata=ElementMetadata(
                source_doc=source_doc,
                page_number=1,
                element_index=element_index,
                element_type="table",
                confidence=1.0,
                extra={
                    "row_count": len(rows),
                    "col_count": len(headers),
                    "headers": headers,
                    "sheet_name": "Sheet1",
                },
            ),
        )


# =====================================================================
# 2. VLM IMAGE CAPTIONING SKILL
# =====================================================================

class VLMCaptioningSkill:
    """Enrich image and chart elements with technical VLM descriptions.

    Design:
    1. If ``captioner`` callable is injected: calls it directly (for tests & custom pipelines).
    2. Else if ``settings.openai_api_key`` is configured: calls Vision LLM using centralized prompts.
    3. Else: uses deterministic fallback based on alt text and image path.
    """

    def __init__(self, captioner: Callable[[ParsedElement], str] | None = None):
        self._captioner = captioner

    def apply(self, element: ParsedElement) -> ParsedElement:
        """Populate ``vlm_caption`` for a validated image or chart element."""
        if element.metadata.element_type not in ("image", "chart"):
            raise ValueError("VLMCaptioningSkill requires an image element (or chart element)")

        # Branch 1: Injected custom captioner callable
        if self._captioner is not None:
            caption = self._captioner(element)
            source_tag = "injected_vlm"
        # Branch 2: Live Vision-Language Model
        elif settings.openai_api_key and settings.openai_api_key != "sk-mock-key-replace-with-actual":
            caption, source_tag = self._call_vlm(element)
        # Branch 3: Deterministic fallback
        else:
            caption = self._fallback_caption(element)
            source_tag = "deterministic_fallback"

        if not isinstance(caption, str) or not caption.strip():
            raise ValueError("VLM captioner must return a non-empty string")

        element.vlm_caption = caption.strip()
        element.metadata.extra["caption_source"] = source_tag
        logger.debug(f"Captioned image element '{element.element_id}' via {source_tag}")
        return element

    def _call_vlm(self, element: ParsedElement) -> tuple[str, str]:
        """Invoke Vision-Language Model via centralized prompts."""
        try:
            vlm = get_vlm_llm(temperature=0.1)
            alt_text = element.metadata.extra.get("alt_text", "visual asset")
            prompt_content = VLM_IMAGE_CAPTIONING_USER_TEMPLATE.format(
                image_source=element.image_path or "embedded",
                document_title=element.metadata.source_doc,
                page_number=element.metadata.page_number,
                surrounding_text=element.metadata.parent_header or alt_text,
            )
            messages = [
                SystemMessage(content=VLM_IMAGE_CAPTIONING_SYSTEM_PROMPT),
                HumanMessage(content=prompt_content),
            ]
            response = vlm.invoke(messages)
            return response.content, "live_vlm"
        except Exception as exc:
            logger.warning(f"Live VLM captioning failed: {exc}. Falling back to deterministic caption.")
            return self._fallback_caption(element), "vlm_fallback_error"

    @staticmethod
    def _fallback_caption(element: ParsedElement) -> str:
        source = element.image_path or "embedded image"
        alt_text = element.metadata.extra.get("alt_text") or "visual asset"
        return f"Image asset '{source}': {alt_text}."


# =====================================================================
# 3. SKILL APPLICATION PIPELINE
# =====================================================================

def _apply_skills_impl(document: ParsedDocument) -> ParsedDocument:
    """Apply deterministic parser skills to all eligible elements in a document."""
    table_skill = TableParsingSkill()
    caption_skill = VLMCaptioningSkill()
    for element in document.elements:
        if element.metadata.element_type == "table":
            table_skill.apply(element)
        elif element.metadata.element_type in ("image", "chart"):
            caption_skill.apply(element)
    return document


# =====================================================================
# 4. FORMAT-SPECIFIC AGENT TOOLS
# =====================================================================

def _serialize_tool_result(value: Any) -> str:
    """Serialize validated Pydantic results for stable ToolMessage content."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False)

@tool
def profile_file_tool(file_path: str) -> str:
    """Inspect a local file and return its validated classification metadata.

    Use this tool as the first operation for file-based ingestion. It computes
    the extension, semantic category, recommended extraction strategy,
    content fingerprint, and feature flags such as likely tables, images, or
    code blocks. It does not parse document content and it does not choose a
    parser on behalf of the agent; the returned ``ProfilerResult`` is the
    evidence the agent uses for its next tool call.

    Args:
        file_path: Existing local path to the source file.

    Returns:
        A JSON serialization of the validated ``ProfilerResult``. JSON tool
        content keeps the result readable to the next LLM review turn.
    """
    return _serialize_tool_result(DocumentProfiler().profile(file_path))


@tool
def profile_content_tool(content: str, file_name: str = "document.md") -> str:
    """Classify source text supplied directly in the graph state.

    Use this tool when there is no local file path, for example when an API
    caller submits Markdown or plain text in ``raw_content``. The filename is
    used only as provenance and a format hint. This tool returns profiler
    evidence; it does not parse or transform the content.

    Args:
        content: Non-empty source text.
        file_name: Logical filename used to infer the text format.

    Returns:
        A JSON serialization of the validated ``ProfilerResult``.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    return _serialize_tool_result(DocumentProfiler().profile_content(content, file_name))


@tool
def mineru_parse_tool(file_path: str, backend: str = "") -> str:
    """Parse PDF, Office, or image files through the configured MinerU API.

    Use this tool for ``.pdf``, ``.docx``, ``.pptx``, ``.xlsx``, and supported
    image files after profiling. The adapter requests Markdown plus
    ``content_list.json`` from MinerU and normalizes the result into this
    project's ``ParsedDocument`` JSON contract. The default backend is the
    configured ``pipeline`` backend. PDF API failures use the native pypdf
    fallback; Office and image failures are returned as fail-fast tool errors
    and must not be silently decoded as text.

    Args:
        file_path: Existing local PDF, Office, or image path.
        backend: Optional MinerU backend override, such as ``pipeline`` or
            ``vlm``. An empty value uses ``MINERU_BACKEND``.

    Returns:
        A JSON serialization of the validated ``ParsedDocument``.
    """
    document = MinerUAdapter().parse_file(file_path, backend=backend or None)
    return _serialize_tool_result(document)


@tool
def parse_markdown_tool(markdown: str, file_name: str = "document.md") -> str:
    """Parse Markdown into ordered, typed elements and section metadata.

    Use this tool for Markdown content after the agent has classified the
    source as Markdown. It recognizes headings, paragraphs, lists, fenced code,
    tables, images, page markers, and nested section paths. It only parses the
    input; table normalization and image captioning are separate tools so the
    agent can verify or repeat those operations independently.

    Args:
        markdown: Non-empty Markdown source.
        file_name: Logical source filename for provenance.

    Returns:
        A JSON serialization of a validated ``ParsedDocument`` with
        ``file_type='markdown'``.
    """
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("markdown must be a non-empty string")
    return _serialize_tool_result(
        DocumentLayoutParser().parse_markdown(markdown, file_name=file_name)
    )


@tool
def parse_text_tool(text: str, file_name: str = "document.txt") -> str:
    """Parse plain text into validated paragraph elements.

    Use this tool for ``.txt``, ``.text``, or ``.log`` sources. Paragraph
    boundaries and source provenance are preserved, while the return value is
    always the strict ``ParsedDocument`` model expected by downstream stages.

    Args:
        text: Non-empty plain-text source.
        file_name: Logical source filename for provenance.

    Returns:
        A JSON serialization of a validated ``ParsedDocument`` with
        ``file_type='txt'``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    return _serialize_tool_result(DocumentLayoutParser().parse_text(text, file_name=file_name))


@tool
def parse_spreadsheet_tool(file_path: str) -> str:
    """Parse a CSV or TSV file into structured table elements.

    Use this tool after profiling identifies a spreadsheet-like delimited
    file. It converts rows into a Markdown table while retaining headers,
    row/column counts, source filename, and deterministic document identity.
    Further table normalization can be requested with
    ``apply_document_skills_tool`` or ``normalize_table_tool``.

    Args:
        file_path: Existing local CSV or TSV path.

    Returns:
        A JSON serialization of a validated ``ParsedDocument`` containing
        table elements.
    """
    extension = os.path.splitext(file_path)[1].lower()
    if extension not in {".csv", ".tsv"}:
        raise ValueError(
            "parse_spreadsheet_tool only accepts .csv or .tsv; "
            f"received {extension or '(no extension)'}"
        )
    return _serialize_tool_result(DocumentLayoutParser().parse_file(file_path))


@tool
def parse_code_tool(file_path: str) -> str:
    """Parse a source-code file into a typed code element.

    Use this tool after profiling identifies a supported source-code format.
    It preserves the original source, filename, inferred language metadata,
    and deterministic document identity without executing the code.

    Args:
        file_path: Existing source-code file path.

    Returns:
        A JSON serialization of a validated ``ParsedDocument`` containing code
        elements.
    """
    return _serialize_tool_result(DocumentLayoutParser().parse_file(file_path))


@tool
def normalize_table_tool(element: ParsedElement) -> str:
    """Normalize one parsed table element while preserving its provenance.

    Use this tool when a parsed document contains a table whose cells or
    metadata need normalization. It accepts and returns one ``ParsedElement``;
    it does not inspect filenames or route the document to another pipeline.

    Args:
        element: A validated table ``ParsedElement``.

    Returns:
        A JSON serialization of the same validated element with canonical
        Markdown rows and table statistics in ``metadata.extra``.
    """
    return _serialize_tool_result(TableParsingSkill().apply(element))


@tool
def vlm_caption_tool(element: ParsedElement) -> str:
    """Add a technical caption to one parsed image or chart element.

    Use this tool when a parsed element represents an image or chart and needs
    searchable semantic text. It uses an injected/live VLM when configured and
    a deterministic metadata-based fallback otherwise. It never selects a
    parser and it never changes element identity or ordering.

    Args:
        element: A validated image or chart ``ParsedElement``.

    Returns:
        A JSON serialization of the same validated element with
        ``vlm_caption`` populated.
    """
    return _serialize_tool_result(VLMCaptioningSkill().apply(element))


@tool
def apply_document_skills_tool(document: ParsedDocument) -> str:
    """Apply eligible table normalization and image captioning to a document.

    This is the canonical agent-facing enrichment tool. Use it after exactly
    one format parser returns a ``ParsedDocument``. It applies table and
    image/chart skills only where their element type is eligible, then returns
    the same validated document contract for final review.

    Args:
        document: Parsed document returned by one of the format parser tools.

    Returns:
        A JSON serialization of the enriched validated ``ParsedDocument``.
    """
    return _serialize_tool_result(_apply_skills_impl(document))


__all__ = [
    "TableParsingSkill",
    "SpreadsheetParsingSkill",
    "VLMCaptioningSkill",
    "profile_file_tool",
    "profile_content_tool",
    "mineru_parse_tool",
    "parse_markdown_tool",
    "parse_text_tool",
    "parse_spreadsheet_tool",
    "parse_code_tool",
    "normalize_table_tool",
    "vlm_caption_tool",
    "apply_document_skills_tool",
]
