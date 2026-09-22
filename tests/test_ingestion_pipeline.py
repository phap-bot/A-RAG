"""Execution and observability tests for the end-to-end ingestion graph."""

from pathlib import Path

from src.ingestion.pipeline import (
    IngestionPipelineRuntime,
    execute_ingestion_pipeline,
    initial_ingestion_state,
)


def test_ingestion_pipeline_emits_nested_chunking_tasks(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# Root\n\nA provenance-safe paragraph.", encoding="utf-8")
    parts: list[dict] = []

    state = execute_ingestion_pipeline(
        initial_ingestion_state(
            IngestionPipelineRuntime(
                file_path=str(source),
                workspace_id="ws_test",
                source_path="uploads/notes.md",
            )
        ),
        on_event=parts.append,
    )

    assert state["pipeline_stage"] == "indexed"
    assert state["validation"]["status"] == "valid"
    assert state["chunks"]

    started = [
        part["data"]["name"]
        for part in parts
        if part.get("type") == "tasks"
        and "input" in part.get("data", {})
    ]
    assert started[:5] == [
        "detect_type",
        "extraction",
        "quality_check",
        "normalize",
        "semantic_chunk",
    ]
    assert {"plan_strategy", "build_chunks", "validate_chunks"}.issubset(started)

    nested = [
        part
        for part in parts
        if part.get("type") == "tasks"
        and part.get("data", {}).get("name") == "plan_strategy"
    ]
    assert nested and nested[0]["ns"]

    custom_kinds = {
        part["data"]["kind"]
        for part in parts
        if part.get("type") == "custom"
    }
    assert {"classification", "tool_started", "tool_finished", "index_validation"}.issubset(custom_kinds)
