"""Unit tests for the active Zone 1 parser skills and agent tools."""

import json

import pytest

from src.core.exceptions import ParsingError
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.models import ElementMetadata, ParsedDocument, ParsedElement
from src.ingestion.parser.skills import (
    SpreadsheetParsingSkill,
    TableParsingSkill,
    VLMCaptioningSkill,
    apply_document_skills_tool,
    mineru_parse_tool,
    normalize_table_tool,
    parse_code_tool,
    parse_markdown_tool,
    parse_spreadsheet_tool,
    parse_text_tool,
    profile_content_tool,
)


@pytest.fixture
def sample_table_element():
    return ParsedElement(
        element_id="elem-table-001",
        content="| Name | Role | Status |\n|---|---|---|\n| Agent1 | Formulator | Active |\n| Agent2 | Synthesizer | Active |",
        metadata=ElementMetadata(
            source_doc="architecture.md",
            element_index=0,
            element_type="table",
        ),
    )


@pytest.fixture
def sample_image_element():
    return ParsedElement(
        element_id="elem-img-001",
        content="[IMAGE: Architecture Workflow]",
        image_path="assets/workflow.png",
        metadata=ElementMetadata(
            source_doc="architecture.md",
            element_index=1,
            element_type="image",
            extra={"alt_text": "Complete system workflow diagram"},
        ),
    )


class TestTableParsingSkill:
    def test_table_normalization(self, sample_table_element):
        enriched = TableParsingSkill().apply(sample_table_element)

        assert enriched.metadata.extra["normalized"] is True
        assert enriched.metadata.extra["row_count"] == 4
        assert enriched.metadata.extra["col_count"] == 3
        assert enriched.metadata.extra["headers"] == ["Name", "Role", "Status"]

    def test_non_table_raises_error(self, sample_image_element):
        with pytest.raises(ValueError, match="requires a table element"):
            TableParsingSkill().apply(sample_image_element)


class TestSpreadsheetParsingSkill:
    def test_parse_csv_content(self):
        csv_data = "Product,Price,Quantity\nWidget A,10.5,100\nWidget B,20.0,50\n"
        element = SpreadsheetParsingSkill().parse_csv(csv_data, source_doc="inventory.csv")

        assert element.metadata.element_type == "table"
        assert element.metadata.extra["row_count"] == 3
        assert element.metadata.extra["col_count"] == 3
        assert element.metadata.extra["headers"] == ["Product", "Price", "Quantity"]
        assert "| Widget A | 10.5 | 100 |" in element.content

    def test_empty_csv_raises_error(self):
        with pytest.raises(ValueError, match="no non-empty rows"):
            SpreadsheetParsingSkill().parse_csv("", source_doc="empty.csv")


class TestVLMCaptioningSkill:
    def test_injected_captioner(self, sample_image_element):
        enriched = VLMCaptioningSkill(
            captioner=lambda _element: "Mock VLM technical diagram caption."
        ).apply(sample_image_element)

        assert enriched.vlm_caption == "Mock VLM technical diagram caption."
        assert enriched.metadata.extra["caption_source"] == "injected_vlm"

    def test_deterministic_fallback_caption(self, sample_image_element):
        enriched = VLMCaptioningSkill().apply(sample_image_element)

        assert enriched.vlm_caption is not None
        assert "assets/workflow.png" in enriched.vlm_caption
        assert "Complete system workflow diagram" in enriched.vlm_caption
        assert enriched.metadata.extra["caption_source"] == "deterministic_fallback"

    def test_non_image_raises_error(self, sample_table_element):
        with pytest.raises(ValueError, match="image element"):
            VLMCaptioningSkill().apply(sample_table_element)


class TestActiveAgentTools:
    def test_tools_have_descriptive_names_and_docstrings(self):
        tools = [
            profile_content_tool,
            mineru_parse_tool,
            parse_markdown_tool,
            parse_text_tool,
            parse_spreadsheet_tool,
            parse_code_tool,
            normalize_table_tool,
            apply_document_skills_tool,
        ]
        assert all(item.name.endswith("_tool") for item in tools)
        assert all(item.description.strip() for item in tools)

    def test_profile_content_tool_returns_profiler_json(self):
        result = json.loads(
            profile_content_tool.invoke(
                {"content": "# Header\n\nBody", "file_name": "test.md"}
            )
        )

        assert result["file_type"] == "md"
        assert result["category"] == "document"
        assert result["recommended_engine"] == "native"

    def test_native_format_tools_return_parsed_document_json(self, tmp_path):
        markdown = json.loads(
            parse_markdown_tool.invoke(
                {"markdown": "# Header\n\nContent paragraph.", "file_name": "test.md"}
            )
        )
        text = json.loads(
            parse_text_tool.invoke({"text": "First paragraph.\n\nSecond paragraph."})
        )

        source = tmp_path / "module.py"
        source.write_text("def answer():\n    return 42\n", encoding="utf-8")
        code = json.loads(parse_code_tool.invoke({"file_path": str(source)}))

        assert ParsedDocument.model_validate(markdown).file_type == "markdown"
        assert ParsedDocument.model_validate(text).file_type == "txt"
        assert ParsedDocument.model_validate(code).elements[0].metadata.element_type == "code"

    def test_spreadsheet_tool_rejects_non_delimited_files(self, tmp_path):
        source = tmp_path / "table.xlsx"
        source.write_bytes(b"not a csv")

        with pytest.raises(ValueError, match="only accepts .csv or .tsv"):
            parse_spreadsheet_tool.invoke({"file_path": str(source)})

    def test_apply_document_skills_tool_returns_enriched_json(
        self, sample_table_element, sample_image_element
    ):
        document = ParsedDocument(
            document_id="doc123",
            file_name="spec.md",
            file_type="md",
            elements=[sample_table_element, sample_image_element],
        )

        result = json.loads(apply_document_skills_tool.invoke({"document": document}))
        enriched = ParsedDocument.model_validate(result)

        assert enriched.elements[0].metadata.extra["normalized"] is True
        assert enriched.elements[1].vlm_caption is not None

    def test_normalize_table_tool_returns_element_json(self, sample_table_element):
        result = json.loads(normalize_table_tool.invoke({"element": sample_table_element}))
        normalized = ParsedElement.model_validate(result)

        assert normalized.metadata.extra["normalized"] is True


class TestLayoutParserCSV:
    def test_parse_csv_file(self, tmp_path):
        source = tmp_path / "metrics.csv"
        source.write_text(
            "Metric,Score,Passed\nFaithfulness,0.95,True\nRelevance,0.90,True\n",
            encoding="utf-8",
        )

        document = DocumentLayoutParser().parse_file(str(source))

        assert document.file_type == "csv"
        assert len(document.elements) == 1
        assert document.elements[0].metadata.element_type == "table"
        assert document.elements[0].metadata.extra["row_count"] == 3


class TestRemovedLegacySurface:
    def test_native_pdf_and_image_parsers_are_internal_only(self, tmp_path):
        import src.ingestion.parser.skills as skills

        assert not hasattr(skills, "parse_pdf_tool")
        assert not hasattr(skills, "parse_image_tool")
        assert not hasattr(skills, "ragflow_ddu_parse")
        assert not hasattr(skills, "layout_parse")
        assert not hasattr(skills, "md_to_json")
        assert not hasattr(skills, "apply_skills")

        image = tmp_path / "diagram.png"
        image.write_bytes(b"png fixture")
        with pytest.raises(ParsingError, match="requires the MinerU pipeline"):
            DocumentLayoutParser().parse_file(str(image))
