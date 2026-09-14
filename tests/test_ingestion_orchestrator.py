"""Tests for the LLM-driven Zone 1 ingestion StateGraph loop."""

import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pypdf import PdfWriter

from src.ingestion.parser.layout_parser import DocumentLayoutParser
from src.core.exceptions import MinerUError
from src.ingestion.parser.models import ParsedDocument
from src.ingestion.parser.orchestrator import (
    ORCHESTRATOR_TOOLS,
    build_ingestion_graph,
    load_skill,
    run_ingestion,
)
from src.ingestion.parser.state import create_initial_state


class ToolAwareFakeChatModel(FakeMessagesListChatModel):
    """Offline fake that accepts the same bind_tools call as a real chat model."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        return self


def _tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _agent_for(raw_content: str, final_document: ParsedDocument) -> FakeMessagesListChatModel:
    """Return a deterministic model that exercises every graph transition."""
    return ToolAwareFakeChatModel(
        responses=[
            _tool_call(
                "profile_content_tool",
                {"content": raw_content, "file_name": "document.md"},
                "profile-1",
            ),
            _tool_call(
                "parse_markdown_tool",
                {"markdown": raw_content, "file_name": "document.md"},
                "parse-1",
            ),
            _tool_call(
                "apply_document_skills_tool",
                {"document": final_document.model_dump(mode="json")},
                "skills-1",
            ),
            AIMessage(content=json.dumps(final_document.model_dump(mode="json"))),
        ]
    )


def test_graph_contains_llm_and_prebuilt_tool_nodes():
    graph = build_ingestion_graph()
    nodes = graph.get_graph().nodes

    assert "orchestrator_node" in nodes
    assert "tools_node" in nodes
    assert "profile_source" not in nodes
    assert "parse_markdown" not in nodes


def test_runtime_skill_is_loadable_from_exact_path():
    content = load_skill("ingestion-orchestrator")

    assert "# Zone 1 Ingestion Orchestrator" in content
    assert "mineru_parse_tool" in content
    assert "ParsedDocument" in content


def test_raw_markdown_runs_llm_tool_loop_and_returns_json():
    raw_content = "# Architecture\n\nThe parser returns JSON."
    document = DocumentLayoutParser().parse_markdown(raw_content, file_name="document.md")
    model = _agent_for(raw_content, document)

    state = run_ingestion(raw_content=raw_content, llm=model)

    assert state["stage"] == "json"
    assert state["pipeline"] == "markdown"
    assert state["parsed_document"].file_type == "markdown"
    assert state["output_json"]["file_name"] == "document.md"
    assert state["output_json"]["profiler_result"]["strategy"] == "direct_text"
    assert state["llm_iterations"] == 4
    assert len(state["messages"]) >= 8


def test_llm_final_json_is_written_to_requested_artifact(tmp_path):
    source = tmp_path / "architecture.md"
    output = tmp_path / "architecture.json"
    raw_content = "# Architecture\n\nPipeline details."
    source.write_text(raw_content, encoding="utf-8")
    document = DocumentLayoutParser().parse_markdown(raw_content, file_name=source.name)
    model = ToolAwareFakeChatModel(
        responses=[
            _tool_call(
                "profile_file_tool",
                {"file_path": str(source)},
                "profile-file-1",
            ),
            _tool_call(
                "parse_markdown_tool",
                {"markdown": raw_content, "file_name": source.name},
                "parse-file-1",
            ),
            AIMessage(content=json.dumps(document.model_dump(mode="json"))),
        ]
    )

    state = run_ingestion(file_path=str(source), output_path=str(output), llm=model)

    assert state["stage"] == "json"
    assert state["pipeline"] == "markdown"
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["file_name"] == "architecture.md"
    assert payload["file_type"] == "markdown"


def test_tools_are_bound_with_descriptive_agent_facing_names():
    names = {tool.name for tool in ORCHESTRATOR_TOOLS}

    assert {
        "profile_file_tool",
        "profile_content_tool",
        "mineru_parse_tool",
        "parse_markdown_tool",
        "parse_text_tool",
        "parse_spreadsheet_tool",
        "parse_code_tool",
        "apply_document_skills_tool",
    }.issubset(names)
    assert all(tool.description.strip() for tool in ORCHESTRATOR_TOOLS)


def test_missing_source_returns_structured_error_without_llm_call():
    state = build_ingestion_graph().invoke(create_initial_state())

    assert state["stage"] == "error"
    assert state["output_json"]["status"] == "error"
    assert "Either file_path or raw_content is required" in state["errors"][0]


def test_unsupported_extension_returns_structured_error(tmp_path):
    source = tmp_path / "archive.xyz"
    source.write_bytes(b"unknown binary")
    model = ToolAwareFakeChatModel(
        responses=[
            _tool_call("profile_file_tool", {"file_path": str(source)}, "profile-unknown-1"),
        ]
    )

    state = run_ingestion(file_path=str(source), llm=model)

    assert state["stage"] == "error"
    assert state["output_json"]["error"]["details"]["policy"] == "unsupported_format"


def test_configured_graph_without_llm_returns_configuration_error(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes", encoding="utf-8")

    state = run_ingestion(file_path=str(source))

    assert state["stage"] == "error"
    assert "LLM must be injected" in state["errors"][0]


def test_mineru_tool_loop_preserves_pdf_file_type(monkeypatch, tmp_path):
    source = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as file_handle:
        writer.write(file_handle)
    document = DocumentLayoutParser().parse_file(str(source))

    class FakeMinerUAdapter:
        def parse_file(self, file_path, backend=None):  # noqa: ANN001, ARG002
            return document

    monkeypatch.setattr("src.ingestion.parser.skills.MinerUAdapter", FakeMinerUAdapter)
    model = ToolAwareFakeChatModel(
        responses=[
            _tool_call("profile_file_tool", {"file_path": str(source)}, "profile-pdf-1"),
            _tool_call("mineru_parse_tool", {"file_path": str(source)}, "parse-pdf-1"),
            AIMessage(content=json.dumps(document.model_dump(mode="json"))),
        ]
    )

    state = run_ingestion(file_path=str(source), llm=model)

    assert state["stage"] == "json"
    assert state["pipeline"] == "mineru"
    assert state["parsed_document"].file_type == "pdf"
    assert state["parsed_document"].total_pages == 1


def test_office_mineru_failure_stops_with_structured_fail_fast_error(monkeypatch, tmp_path):
    source = tmp_path / "failed.docx"
    source.write_bytes(b"office fixture")

    class FailingMinerUAdapter:
        def parse_file(self, file_path, backend=None):  # noqa: ANN001, ARG002
            raise MinerUError(
                "MinerU API offline",
                details={"backend": backend or "pipeline"},
            )

    monkeypatch.setattr("src.ingestion.parser.skills.MinerUAdapter", FailingMinerUAdapter)
    model = ToolAwareFakeChatModel(
        responses=[
            _tool_call("profile_file_tool", {"file_path": str(source)}, "profile-office-1"),
            _tool_call("mineru_parse_tool", {"file_path": str(source)}, "parse-office-1"),
        ]
    )

    state = run_ingestion(file_path=str(source), llm=model)

    assert state["stage"] == "error"
    assert state["output_json"]["error"]["error_type"] == "MinerUError"
    assert state["output_json"]["error"]["details"]["fallback"] == "none"
    assert "fail-fast" in state["output_json"]["error"]["message"]
