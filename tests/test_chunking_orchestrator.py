"""Zone 1 → Knowledge Base chunking graph tests."""

import json

import pytest
from langchain_core.messages import AIMessage

from src.ingestion.chunking import ChunkPlan, load_skill, run_chunking
from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.ingestion.parser.models import ElementMetadata, ParsedDocument, ParsedElement


class FakePlanner:
    """Small provider-neutral fake for the strict planner boundary."""

    def __init__(self, payload):
        self.payload = payload
        self.messages = []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return AIMessage(content=json.dumps(self.payload))


def test_runtime_skill_is_loaded_from_repository():
    skill = load_skill("chunking-orchestrator")

    assert "Think before coding" in skill
    assert "parent_chunk_id" in skill


def test_planner_receives_metadata_only_and_executor_resolves_element_ids():
    document = DocumentLayoutParser().parse_markdown(
        "# Root\n\nSECRET SOURCE TEXT", file_name="plan.md"
    )
    planner = FakePlanner(
        {
            "strategy": "hierarchical_semantic",
            "groups": [
                {"element_ids": [element.element_id for element in document.elements]}
            ],
        }
    )

    result = run_chunking(document, max_tokens=40, overlap_tokens=0, planner_llm=planner)

    assert result["stage"] == "validated"
    assert result["planner_mode"] == "llm"
    assert isinstance(result["plan"], ChunkPlan)
    assert "SECRET SOURCE TEXT" not in planner.messages[0][1].content
    assert result["chunks"][0].content.endswith("SECRET SOURCE TEXT")


def test_invalid_planner_payload_falls_back_without_allowing_content_field():
    document = DocumentLayoutParser().parse_text("source", file_name="notes.txt")
    planner = FakePlanner(
        {
            "strategy": "token_window",
            "groups": [{"element_ids": [document.elements[0].element_id]}],
            "content": "rewrite this source",
        }
    )

    result = run_chunking(document, max_tokens=40, overlap_tokens=0, planner_llm=planner)

    assert result["stage"] == "validated"
    assert result["planner_mode"] == "deterministic_fallback"
    assert result["planner_warnings"]
    assert result["chunks"][0].content.endswith("source")


def test_markdown_chunks_preserve_hierarchy_and_reverse_indexes():
    document = DocumentLayoutParser().parse_markdown(
        """# Root

Introduction to the document.

## Child

Details that belong to the child section.
""",
        file_name="hierarchy.md",
    )

    result = run_chunking(document, max_tokens=40, overlap_tokens=0)

    assert result["stage"] == "validated"
    assert result["strategy"] == "hierarchical_semantic"
    assert result["validation"]["coverage_complete"] is True
    assert result["validation"]["source_element_count"] == len(document.elements)
    assert result["element_chunk_map"]
    assert result["section_chunk_map"]
    assert all(chunk.metadata.document_id == document.document_id for chunk in result["chunks"])
    assert all(chunk.token_estimate <= 40 for chunk in result["chunks"])

    child_chunks = [
        chunk for chunk in result["chunks"] if chunk.metadata.section_path[-1:] == ["Child"]
    ]
    root_chunks = [
        chunk for chunk in result["chunks"] if chunk.metadata.section_path == ["Root"]
    ]
    assert child_chunks and root_chunks
    assert child_chunks[0].metadata.parent_chunk_id == root_chunks[0].chunk_id


def test_oversized_text_is_split_within_budget_and_is_deterministic():
    text = " ".join(f"word-{index}" for index in range(120))
    document = DocumentLayoutParser().parse_text(text, file_name="large.txt")

    first = run_chunking(document, max_tokens=24, overlap_tokens=3)
    second = run_chunking(document, max_tokens=24, overlap_tokens=3)

    assert first["stage"] == "validated"
    assert first["strategy"] == "token_window"
    assert len(first["chunks"]) > 1
    assert [chunk.chunk_id for chunk in first["chunks"]] == [
        chunk.chunk_id for chunk in second["chunks"]
    ]
    assert all(chunk.token_estimate <= 24 for chunk in first["chunks"])
    assert first["validation"]["coverage_complete"] is True


def test_csv_uses_row_windows_and_repeats_headers():
    document = DocumentLayoutParser().parse_csv(
        "name,role\nAlice,Researcher\nBob,Engineer\n",
        file_name="people.csv",
    )

    result = run_chunking(document, max_tokens=40, overlap_tokens=0)

    assert result["stage"] == "validated"
    assert result["strategy"] == "row_window"
    assert len(result["chunks"]) == 1
    chunk = result["chunks"][0]
    assert chunk.metadata.modality == "table"
    assert chunk.metadata.has_table is True
    assert chunk.metadata.extra["header_repeated"] is True
    assert "| name | role |" in chunk.content


def test_code_strategy_preserves_line_boundaries_and_language():
    source = "\n".join(
        [
            "def calculate(value):",
            "    result = value * 2",
            "    return result",
        ]
        * 20
    )
    document = DocumentLayoutParser().parse_code(source, file_name="calc.py")

    result = run_chunking(document, max_tokens=24, overlap_tokens=0)

    assert result["stage"] == "validated"
    assert result["strategy"] == "code_boundary"
    assert all(chunk.metadata.modality == "code" for chunk in result["chunks"])
    assert all(chunk.metadata.language == "py" for chunk in result["chunks"])
    assert all("\n" in chunk.code_snippet for chunk in result["chunks"][:-1])
    assert all(chunk.token_estimate <= 24 for chunk in result["chunks"])


def test_layout_strategy_does_not_mix_source_pages():
    document = ParsedDocument(
        document_id="pdf-doc",
        file_name="layout.pdf",
        file_type="pdf",
        total_pages=2,
        elements=[
            ParsedElement(
                element_id="pdf-doc-elem-0000",
                content="Page one content.",
                metadata=ElementMetadata(
                    source_doc="layout.pdf",
                    page_number=1,
                    element_index=0,
                    element_type="text",
                ),
            ),
            ParsedElement(
                element_id="pdf-doc-elem-0001",
                content="Page two content.",
                metadata=ElementMetadata(
                    source_doc="layout.pdf",
                    page_number=2,
                    element_index=1,
                    element_type="text",
                ),
            ),
        ],
    )

    result = run_chunking(document, max_tokens=40, overlap_tokens=0)

    assert result["stage"] == "validated"
    assert result["strategy"] == "page_element"
    assert [chunk.metadata.page_numbers for chunk in result["chunks"]] == [[1], [2]]


def test_invalid_budget_is_rejected_before_graph_execution():
    document = DocumentLayoutParser().parse_text("content", file_name="x.txt")

    with pytest.raises(ValueError, match="at least 16"):
        run_chunking(document, max_tokens=8)
