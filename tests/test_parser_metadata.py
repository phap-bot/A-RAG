"""Comprehensive Unit Tests for Step 1 of Ingestion Zone:
Parser models.py — ProfilerResult, BoundingBox validation, ParsedDocument auto-stats,
ContentChunk final contract, and section_path/parent_header hierarchy tracking.
"""

import json

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from src.ingestion.parser.models import (
    BoundingBox,
    ChunkMetadata,
    ContentChunk,
    ElementMetadata,
    ExtractionStrategy,
    FileCategory,
    FileFingerprint,
    ModalityType,
    ParsedDocument,
    ParsedElement,
    ProfilerResult,
    generate_doc_id,
    generate_element_id,
)
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.skills import (
    TableParsingSkill,
    VLMCaptioningSkill,
    apply_document_skills_tool,
    parse_markdown_tool,
)


# =====================================================================
# 1. PROFILER RESULT TESTS
# =====================================================================

class TestProfilerResult:
    def test_profiler_result_valid(self):
        """Valid profiler classification with all feature flags."""
        result = ProfilerResult(
            file_name="architecture_spec.pdf",
            file_type="pdf",
            category=FileCategory.DOCUMENT,
            strategy=ExtractionStrategy.LAYOUT_ANALYSIS,
            fingerprint=FileFingerprint(
                sha256="a1b2c3d4e5f6" * 5 + "a1b2c3d4",
                size_bytes=1_048_576,
                mime_type="application/pdf",
            ),
            has_tables=True,
            has_images=True,
            has_code_blocks=False,
            estimated_pages=12,
            language_hint="vi",
            profiler_confidence=0.97,
        )
        assert result.category == FileCategory.DOCUMENT
        assert result.strategy == ExtractionStrategy.LAYOUT_ANALYSIS
        assert result.has_tables is True
        assert result.fingerprint.size_bytes == 1_048_576

    def test_profiler_spreadsheet(self):
        """Spreadsheet file classification with table extraction strategy."""
        result = ProfilerResult(
            file_name="data.xlsx",
            file_type="xlsx",
            category=FileCategory.SPREADSHEET,
            strategy=ExtractionStrategy.TABLE_EXTRACTION,
            fingerprint=FileFingerprint(sha256="abcd" * 16, size_bytes=500),
            has_tables=True,
        )
        assert result.category == FileCategory.SPREADSHEET
        assert result.strategy == ExtractionStrategy.TABLE_EXTRACTION

    def test_profiler_confidence_bounds(self):
        """Confidence must be in [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            ProfilerResult(
                file_name="test.pdf",
                file_type="pdf",
                category=FileCategory.DOCUMENT,
                strategy=ExtractionStrategy.DIRECT_TEXT,
                fingerprint=FileFingerprint(sha256="x" * 64, size_bytes=100),
                profiler_confidence=1.5,
            )


# =====================================================================
# 2. BOUNDING BOX VALIDATION TESTS
# =====================================================================

class TestBoundingBox:
    def test_valid_bounding_box(self):
        box = BoundingBox(x1=0.1, y1=0.2, x2=0.9, y2=0.8)
        assert box.area == pytest.approx(0.8 * 0.6)

    def test_invalid_x_ordering(self):
        """x2 must be >= x1."""
        with pytest.raises(ValidationError):
            BoundingBox(x1=0.9, y1=0.1, x2=0.1, y2=0.9)

    def test_invalid_y_ordering(self):
        """y2 must be >= y1."""
        with pytest.raises(ValidationError):
            BoundingBox(x1=0.0, y1=0.9, x2=1.0, y2=0.1)

    def test_zero_area_box(self):
        """Point box (zero area) is valid."""
        box = BoundingBox(x1=0.5, y1=0.5, x2=0.5, y2=0.5)
        assert box.area == 0.0


# =====================================================================
# 3. ELEMENT METADATA WITH SECTION PATH
# =====================================================================

class TestElementMetadata:
    def test_metadata_with_section_path(self):
        meta = ElementMetadata(
            source_doc="spec.pdf",
            element_index=5,
            element_type="text",
            parent_header="Zone 1 Architecture",
            section_path=["Enterprise RAG", "Zone 1 Architecture"],
        )
        assert meta.parent_header == "Zone 1 Architecture"
        assert len(meta.section_path) == 2

    def test_header_level_constraint(self):
        """header_level must be between 1 and 6."""
        meta = ElementMetadata(
            source_doc="doc.md",
            element_index=0,
            element_type="header",
            header_level=3,
        )
        assert meta.header_level == 3

        with pytest.raises(ValidationError):
            ElementMetadata(
                source_doc="doc.md",
                element_index=0,
                element_type="header",
                header_level=7,
            )


# =====================================================================
# 4. PARSED DOCUMENT AUTO-STATS
# =====================================================================

class TestParsedDocumentStats:
    def test_auto_computed_stats(self):
        """ParsedDocument must auto-compute type_counts and feature flags."""
        elements = [
            ParsedElement(
                element_id="doc-elem-0000",
                content="Introduction",
                metadata=ElementMetadata(source_doc="test.md", element_index=0, element_type="header"),
            ),
            ParsedElement(
                element_id="doc-elem-0001",
                content="Paragraph content here.",
                metadata=ElementMetadata(source_doc="test.md", element_index=1, element_type="text"),
            ),
            ParsedElement(
                element_id="doc-elem-0002",
                content="| A | B |\n|---|---|\n| 1 | 2 |",
                metadata=ElementMetadata(source_doc="test.md", element_index=2, element_type="table"),
            ),
            ParsedElement(
                element_id="doc-elem-0003",
                content="[IMAGE: diagram]",
                metadata=ElementMetadata(source_doc="test.md", element_index=3, element_type="image"),
            ),
        ]

        doc = ParsedDocument(
            document_id="test123",
            file_name="test.md",
            file_type="markdown",
            elements=elements,
        )

        assert doc.stats["total_elements"] == 4
        assert doc.stats["type_counts"]["header"] == 1
        assert doc.stats["type_counts"]["text"] == 1
        assert doc.stats["type_counts"]["table"] == 1
        assert doc.stats["type_counts"]["image"] == 1
        assert doc.stats["has_tables"] is True
        assert doc.stats["has_images"] is True
        assert doc.stats["has_code"] is False


# =====================================================================
# 5. CONTENT CHUNK CONTRACT (FINAL OUTPUT)
# =====================================================================

class TestContentChunk:
    def test_content_chunk_creation(self):
        """ContentChunk must be the ONLY model crossing parser/ boundary."""
        chunk = ContentChunk(
            content="The MCP Gateway exposes JSON-RPC tools for knowledge retrieval.",
            metadata=ChunkMetadata(
                document_id="abc123",
                source_doc="architecture.pdf",
                page_numbers=[3, 4],
                element_ids=["abc123-elem-0005", "abc123-elem-0006"],
                modality="text",
                section_path=["Architecture", "Zone 3"],
                parent_header="Zone 3",
            ),
        )
        assert chunk.chunk_id.startswith("chunk-")
        assert len(chunk.chunk_id) > 10
        assert chunk.metadata.document_id == "abc123"
        assert chunk.metadata.modality == "text"
        assert chunk.metadata.section_path == ["Architecture", "Zone 3"]
        assert chunk.token_estimate > 0

    def test_content_chunk_with_table(self):
        chunk = ContentChunk(
            content="Component comparison table: MCP Gateway vs Direct DB.",
            metadata=ChunkMetadata(
                document_id="doc1",
                source_doc="spec.xlsx",
                modality="table",
                has_table=True,
            ),
            table_markdown="| Component | Protocol |\n|---|---|\n| MCP | JSON-RPC |",
        )
        assert chunk.metadata.has_table is True
        assert chunk.table_markdown is not None

    def test_content_chunk_with_vlm(self):
        chunk = ContentChunk(
            content="Architecture diagram showing 4 isolated zones.",
            metadata=ChunkMetadata(
                document_id="doc2",
                source_doc="arch.pdf",
                modality="image",
                has_image=True,
            ),
            vlm_caption="Diagram depicts Ingestion, Agentic Core, MCP Gateway, and Evaluation zones connected by JSON-RPC.",
        )
        assert chunk.metadata.has_image is True
        assert chunk.vlm_caption is not None

    def test_content_chunk_empty_content_rejected(self):
        """Content must have min_length=1."""
        with pytest.raises(ValidationError):
            ContentChunk(
                content="",
                metadata=ChunkMetadata(document_id="x", source_doc="y"),
            )

    def test_content_chunk_timestamp(self):
        chunk = ContentChunk(
            content="Test content.",
            metadata=ChunkMetadata(document_id="ts", source_doc="test.md"),
        )
        assert isinstance(chunk.created_at, datetime)


# =====================================================================
# 6. LAYOUT PARSER WITH SECTION_PATH TRACKING
# =====================================================================

HIERARCHICAL_MARKDOWN = """# Enterprise Knowledge BaaS

Overview paragraph.

## Zone 1: Ingestion Pipeline

Ingestion details here.

### Sub-system: Parser

Parser specific paragraph.

| Component | Role |
|---|---|
| Profiler | Classify files |
| Skills | Extract content |

## Zone 2: Agentic Core

Agentic core description.

### Sub-system: LangGraph

LangGraph orchestration details.
"""


class TestLayoutParserSectionPath:
    def test_section_path_propagation(self):
        """Verify header hierarchy flows into child element section_path."""
        parser = DocumentLayoutParser()
        doc = parser.parse_markdown(HIERARCHICAL_MARKDOWN, file_name="hierarchy.md")

        # Find elements under "Zone 1: Ingestion Pipeline" > "Sub-system: Parser"
        parser_text = [e for e in doc.elements if e.content == "Parser specific paragraph."]
        assert len(parser_text) == 1
        meta = parser_text[0].metadata
        assert meta.parent_header == "Sub-system: Parser"
        assert meta.section_path == [
            "Enterprise Knowledge BaaS",
            "Zone 1: Ingestion Pipeline",
            "Sub-system: Parser",
        ]

    def test_header_stack_reset_on_same_level(self):
        """When Zone 2 header appears, Zone 1's sub-headers are cleared."""
        parser = DocumentLayoutParser()
        doc = parser.parse_markdown(HIERARCHICAL_MARKDOWN, file_name="hierarchy.md")

        # LangGraph section should NOT have Zone 1's sub-headers
        langgraph_text = [e for e in doc.elements if "LangGraph orchestration" in e.content]
        assert len(langgraph_text) == 1
        meta = langgraph_text[0].metadata
        assert "Zone 1: Ingestion Pipeline" not in meta.section_path
        assert meta.section_path == [
            "Enterprise Knowledge BaaS",
            "Zone 2: Agentic Core",
            "Sub-system: LangGraph",
        ]

    def test_table_inherits_parent_header(self):
        """Table under 'Sub-system: Parser' must inherit that header."""
        parser = DocumentLayoutParser()
        doc = parser.parse_markdown(HIERARCHICAL_MARKDOWN, file_name="hierarchy.md")

        tables = [e for e in doc.elements if e.metadata.element_type == "table"]
        assert len(tables) == 1
        assert tables[0].metadata.parent_header == "Sub-system: Parser"
        assert "Sub-system: Parser" in tables[0].metadata.section_path

    def test_parsed_document_stats_auto_computed(self):
        """ParsedDocument stats must be populated automatically."""
        parser = DocumentLayoutParser()
        doc = parser.parse_markdown(HIERARCHICAL_MARKDOWN, file_name="hierarchy.md")

        assert doc.stats["total_elements"] > 0
        assert doc.stats["has_tables"] is True
        assert "header" in doc.stats["type_counts"]
        assert "text" in doc.stats["type_counts"]


# =====================================================================
# 7. UTILITY FUNCTIONS
# =====================================================================

class TestUtilities:
    def test_generate_doc_id_deterministic(self):
        data = b"Hello Enterprise RAG"
        id1 = generate_doc_id(data)
        id2 = generate_doc_id(data)
        assert id1 == id2
        assert len(id1) == 16

    def test_generate_doc_id_unique(self):
        id1 = generate_doc_id(b"file_a")
        id2 = generate_doc_id(b"file_b")
        assert id1 != id2

    def test_generate_element_id_format(self):
        eid = generate_element_id("abc123", 42)
        assert eid == "abc123-elem-0042"


class TestParserSkills:
    def test_langchain_tools_expose_descriptive_names(self):
        assert parse_markdown_tool.name == "parse_markdown_tool"
        assert apply_document_skills_tool.name == "apply_document_skills_tool"
        assert "validated" in parse_markdown_tool.description.lower()

    def test_markdown_tool_returns_serialized_parsed_document(self):
        result = json.loads(
            parse_markdown_tool.invoke(
            {
                "markdown": "# Title\n\nA paragraph.",
                "file_name": "input.md",
            }
            )
        )

        document = ParsedDocument.model_validate(result)
        assert document.file_name == "input.md"
        assert document.elements[0].metadata.element_type == "header"

    def test_markdown_tool_rejects_empty_markdown(self):
        with pytest.raises(ValueError, match="non-empty"):
            parse_markdown_tool.invoke({"markdown": "   "})

    def test_table_skill_normalizes_cells_and_preserves_metadata(self):
        parser = DocumentLayoutParser()
        document = parser.parse_markdown(
            "# Report\n\n| Name | Value |\n| --- | --- |\n|  A  | 1 |\n",
            file_name="report.md",
        )
        table = next(e for e in document.elements if e.metadata.element_type == "table")

        result = TableParsingSkill().apply(table)

        assert result is table
        assert result.content == "| Name | Value |\n| --- | --- |\n| A | 1 |"
        assert result.metadata.extra["row_count"] == 3
        assert result.metadata.extra["col_count"] == 2

    def test_table_skill_rejects_non_table_element(self):
        parser = DocumentLayoutParser()
        text = parser.parse_text("A paragraph.").elements[0]

        with pytest.raises(ValueError, match="table element"):
            TableParsingSkill().apply(text)

    def test_vlm_skill_fallback_populates_image_caption(self):
        parser = DocumentLayoutParser()
        image = parser.parse_markdown(
            "![Workflow diagram](assets/workflow.png)", file_name="arch.md"
        ).elements[0]

        result = VLMCaptioningSkill().apply(image)

        assert result is image
        assert result.vlm_caption == "Image asset 'assets/workflow.png': Workflow diagram."
        assert result.metadata.extra["caption_source"] == "deterministic_fallback"

    def test_vlm_skill_rejects_non_image_element(self):
        parser = DocumentLayoutParser()
        text = parser.parse_text("A paragraph.").elements[0]

        with pytest.raises(ValueError, match="image element"):
            VLMCaptioningSkill().apply(text)

    def test_apply_document_skills_enriches_document_without_changing_element_order(self):
        parser = DocumentLayoutParser()
        document = parser.parse_markdown(
            "| Name | Value |\n| --- | --- |\n| A | 1 |\n\n![Diagram](diagram.png)\n",
            file_name="mixed.md",
        )
        original_ids = [element.element_id for element in document.elements]

        result = json.loads(apply_document_skills_tool.invoke({"document": document}))
        result = ParsedDocument.model_validate(result)

        assert [element.element_id for element in result.elements] == original_ids
        assert any(element.vlm_caption for element in result.elements)
