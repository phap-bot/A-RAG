"""LLM-driven Zone 1 ingestion orchestration.

The orchestrator is intentionally small: the runtime skill describes the
format policy, the LLM selects a single parser tool, and LangGraph's
``ToolNode`` executes the deterministic parser implementation. The LLM then
reviews the tool result and either calls an enrichment/correction tool or
returns the final validated JSON document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.config import logger, settings
from src.core.exceptions import AppException, ConfigurationError, IngestionError, MinerUError
from src.core.llm_client import get_chat_llm
from src.ingestion.parser.mineru_adapter import MINERU_OFFICE_IMAGE_EXTENSIONS
from src.ingestion.parser.models import ParsedDocument, ProfilerResult
from src.ingestion.parser.skills import (
    apply_document_skills_tool,
    mineru_parse_tool,
    normalize_table_tool,
    parse_code_tool,
    parse_markdown_tool,
    parse_spreadsheet_tool,
    parse_text_tool,
    profile_content_tool,
    profile_file_tool,
    vlm_caption_tool,
)
from src.ingestion.parser.state import AgentState, create_initial_state


# This is the complete set of actions exposed to the orchestrator LLM. The
# tools contain execution logic only; format routing remains a model decision
# grounded by the runtime SKILL.md and profiler output.
ORCHESTRATOR_TOOLS = [
    profile_file_tool,
    profile_content_tool,
    mineru_parse_tool,
    parse_markdown_tool,
    parse_text_tool,
    parse_code_tool,
    parse_spreadsheet_tool,
    apply_document_skills_tool,
    normalize_table_tool,
    vlm_caption_tool,
]

# Backward-compatible name used by earlier parser integrations.
PARSER_TOOLS = ORCHESTRATOR_TOOLS


def load_skill(skill_name: str) -> str:
    """Load the exact runtime skill used by the ingestion orchestrator.

    Args:
        skill_name: Skill directory name under ``.vibeflow/skills``.

    Returns:
        The complete UTF-8 contents of ``SKILL.md``.

    Raises:
        ValueError: If a skill outside the ingestion orchestrator scope is
            requested.
        FileNotFoundError: If the required skill file is missing.
    """
    if skill_name != "ingestion-orchestrator":
        raise ValueError(
            "Zone 1 orchestrator only permits the 'ingestion-orchestrator' skill"
        )

    project_root = Path(__file__).resolve().parents[3]
    skill_path = project_root / ".vibeflow" / "skills" / skill_name / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"Runtime skill file not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def _build_system_message(skill_name: str) -> SystemMessage:
    """Create the system instruction with the runtime skill injected inline."""
    skill_content = load_skill(skill_name)
    return SystemMessage(
        content=(
            "You are the Zone 1 ingestion orchestrator. Execute the source "
            "processing task by calling the bound tools and reviewing their "
            "results. The runtime skill below is authoritative for scope and "
            "format policy. Do not route in prose or invent parsed data. "
            "When the contract is satisfied, respond with only a JSON object "
            "that validates as ParsedDocument.\n\n"
            "===== RUNTIME SKILL: "
            f"{skill_name} =====\n"
            f"{skill_content}\n"
            "===== END RUNTIME SKILL ====="
        )
    )


def _source_request(state: AgentState) -> HumanMessage:
    """Describe the graph input without embedding parser routing in code."""
    file_path = state.get("file_path")
    raw_content = state.get("raw_content", "")
    parts = [
        "Process this ingestion request according to the runtime skill.",
        f"file_path: {file_path or '(none)'}",
        f"raw_content_present: {bool(raw_content.strip())}",
    ]
    if raw_content.strip():
        parts.append("raw_content:\n" + raw_content)
    parts.append(
        "Use the profiler tool first when classification evidence is needed. "
        "After parsing and any required enrichment, return the final JSON."
    )
    return HumanMessage(content="\n\n".join(parts))


def _message_text(content: Any) -> str:
    """Normalize common LangChain message content shapes into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "\n".join(chunks)
    return str(content)


def _json_candidates(text: str) -> list[str]:
    """Return likely JSON payloads from a model response."""
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidates.append("\n".join(lines).strip())

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])

    # Preserve insertion order while avoiding duplicate parse attempts.
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _extract_parsed_document(content: Any) -> ParsedDocument | None:
    """Validate a model's final JSON response as the parser contract."""
    if isinstance(content, ParsedDocument):
        return content

    if isinstance(content, dict):
        payloads = [content]
    else:
        payloads = []
        for candidate in _json_candidates(_message_text(content)):
            try:
                loaded = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                payloads.append(loaded)

    for payload in payloads:
        nested = payload.get("parsed_document") or payload.get("document")
        candidate = nested if isinstance(nested, dict) else payload
        try:
            return ParsedDocument.model_validate(candidate)
        except Exception:
            continue
    return None


def _recover_profiler_result(messages: list[Any]) -> ProfilerResult | None:
    """Recover profiler evidence from a ToolMessage for final provenance."""
    for message in reversed(messages):
        if getattr(message, "type", None) != "tool":
            continue
        if getattr(message, "name", None) not in {
            "profile_file_tool",
            "profile_content_tool",
        }:
            continue
        for candidate in _json_candidates(_message_text(getattr(message, "content", ""))):
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            try:
                return ProfilerResult.model_validate(value)
            except Exception:
                continue
    return None


def _pipeline_from_tool_calls(response: Any) -> str | None:
    """Record the parser tool the LLM actually selected for audit metadata.

    This is deliberately not a routing table: it never chooses or invokes a
    parser. It only derives a human-readable pipeline label from the tool call
    already emitted by the model.
    """
    for call in getattr(response, "tool_calls", []) or []:
        name = call.get("name", "")
        if name == "mineru_parse_tool":
            return "mineru"
        if name.startswith("parse_") and name.endswith("_tool"):
            return name.removeprefix("parse_").removesuffix("_tool")
    return None


def _mineru_fail_fast_error(state: AgentState) -> MinerUError | None:
    """Stop after an Office/Image MinerU error without another LLM decision."""
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    if (
        getattr(last_message, "type", None) != "tool"
        or getattr(last_message, "name", None) != "mineru_parse_tool"
        or getattr(last_message, "status", None) != "error"
    ):
        return None

    file_path = state.get("file_path") or ""
    extension = Path(file_path).suffix.lower().lstrip(".")
    if extension not in MINERU_OFFICE_IMAGE_EXTENSIONS:
        return None
    return MinerUError(
        f"MinerU is required for '.{extension}' and returned an error; "
        "Office/Image fallback is disabled by policy (fail-fast)",
        details={
            "file_path": file_path,
            "extension": extension,
            "fallback": "none",
            "policy": "fail_fast",
        },
    )


def _unsupported_source_error(state: AgentState) -> IngestionError | None:
    """Reject formats for which the active Zone 1 tool contract has no parser."""
    messages = state.get("messages", [])
    profiler_result = state.get("profiler_result") or _recover_profiler_result(
        list(messages)
    )
    if profiler_result is None or profiler_result.recommended_engine != "unsupported":
        return None
    return IngestionError(
        f"No active Zone 1 parser is configured for '.{profiler_result.file_type}'",
        details={
            "file_name": profiler_result.file_name,
            "file_type": profiler_result.file_type,
            "category": profiler_result.category.value,
            "recommended_engine": profiler_result.recommended_engine,
            "policy": "unsupported_format",
        },
    )


def _write_json_output(payload: dict[str, Any], output_path: str) -> None:
    """Write a UTF-8, human-readable JSON artifact after validation."""
    path = Path(output_path)
    if path.suffix.lower() != ".json":
        raise ValueError(f"output_path must end with '.json': {output_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _error_update(state: AgentState, exc: Exception | str) -> dict[str, Any]:
    """Convert orchestration failures into the stable JSON error contract."""
    if isinstance(exc, AppException):
        error_record = exc.to_dict()
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        error_record = {
            "error_type": type(exc).__name__ if not isinstance(exc, str) else "OrchestratorError",
            "message": str(exc),
            "details": {},
        }
    errors = [*state.get("errors", []), error]
    profiler_result = state.get("profiler_result") or _recover_profiler_result(
        list(state.get("messages", []))
    )
    payload: dict[str, Any] = {
        "status": "error",
        "file_path": state.get("file_path"),
        "pipeline": state.get("pipeline"),
        "profiler_result": (
            profiler_result.model_dump(mode="json") if profiler_result else None
        ),
        "error": error_record,
        "errors": errors,
    }
    try:
        output_path = state.get("output_path")
        if output_path:
            _write_json_output(payload, output_path)
    except Exception as write_exc:
        errors.append(f"{type(write_exc).__name__}: {write_exc}")
        payload["errors"] = errors
    return {"stage": "error", "errors": errors, "output_json": payload}


def orchestrator_node(
    state: AgentState,
    llm: BaseChatModel | None = None,
) -> dict[str, Any]:
    """Run one LLM decision/review turn with the runtime skill injected.

    The node never calls a parser directly. It binds the deterministic tools
    to the configured model, sends the skill as a ``SystemMessage``, and
    returns the model's ``AIMessage`` to the graph. A tool-call response keeps
    the graph in the loop; a tool-free response must be valid ``ParsedDocument``
    JSON before the node marks the state complete.
    """
    fail_fast_error = _mineru_fail_fast_error(state)
    if fail_fast_error:
        return _error_update(state, fail_fast_error)
    unsupported_error = _unsupported_source_error(state)
    if unsupported_error:
        return _error_update(state, unsupported_error)

    iteration = int(state.get("llm_iterations", 0))
    max_iterations = int(state.get("max_llm_iterations", 8))
    if iteration >= max_iterations:
        return _error_update(state, f"Maximum orchestrator iterations exceeded: {max_iterations}")

    try:
        if not state.get("file_path") and not state.get("raw_content", "").strip():
            raise ValueError("Either file_path or raw_content is required")

        skill_name = state.get("skill_name") or "ingestion-orchestrator"
        model = llm
        if model is None:
            configured_key = settings.openai_api_key
            if not configured_key or configured_key in {
                "sk-mock-key-replace-with-actual",
                "sk-mock-placeholder-key",
                "[REDACTED:openai-key]",
            }:
                raise ConfigurationError(
                    "An LLM must be injected for tests/local offline runs or "
                    "OPENAI_API_KEY must be configured for production ingestion"
                )
            model = get_chat_llm(temperature=0.0)

        bound_llm = model.bind_tools(ORCHESTRATOR_TOOLS)
        messages = list(state.get("messages", []))
        first_turn = not messages
        if not messages:
            messages = [_build_system_message(skill_name), _source_request(state)]
        elif not any(getattr(message, "type", None) == "system" for message in messages):
            messages.insert(0, _build_system_message(skill_name))

        response = bound_llm.invoke(messages)
        profiler_result = state.get("profiler_result") or _recover_profiler_result(messages)
        updates: dict[str, Any] = {
            # Persist the initial SystemMessage/HumanMessage in state. This is
            # important because ToolNode and every later review turn must see
            # the same runtime skill and source request.
            "messages": [*messages, response] if first_turn else [response],
            "llm_iterations": iteration + 1,
            "skill_name": skill_name,
        }
        if profiler_result is not None:
            updates["profiler_result"] = profiler_result
        selected_pipeline = _pipeline_from_tool_calls(response)
        if selected_pipeline:
            updates["pipeline"] = selected_pipeline
        if getattr(response, "tool_calls", None):
            if iteration + 1 >= max_iterations:
                return {
                    **updates,
                    **_error_update(
                        state,
                        f"Maximum orchestrator iterations exceeded: {max_iterations}",
                    ),
                }
            return updates

        document = _extract_parsed_document(getattr(response, "content", response))
        if document is None:
            return {
                **updates,
                **_error_update(
                    state,
                    "Orchestrator returned no tool call and its response did not "
                    "validate as ParsedDocument JSON",
                ),
            }

        profiler_result = profiler_result or _recover_profiler_result([*messages, response])
        if profiler_result is not None:
            document.profiler_result = profiler_result

        payload = document.model_dump(mode="json")
        output_path = state.get("output_path")
        if output_path:
            _write_json_output(payload, output_path)
        return {
            **updates,
            "profiler_result": profiler_result,
            "parsed_document": document,
            "output_json": payload,
            "stage": "json",
        }
    except Exception as exc:
        logger.error(f"Ingestion orchestrator turn failed: {type(exc).__name__}: {exc}")
        return _error_update(state, exc)


def _route_after_orchestrator(state: AgentState) -> str:
    """Route only on the LLM response shape, never on file extensions in code."""
    if state.get("stage") in {"json", "error"}:
        return "end"
    if int(state.get("llm_iterations", 0)) >= int(state.get("max_llm_iterations", 8)):
        return "end"
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "end"


# LangGraph's prebuilt ToolNode is the execution node. It resolves the tool
# name emitted by the LLM, validates arguments against each @tool schema, and
# appends ToolMessage results to AgentState.messages. Tool failures are returned
# as error ToolMessages so the next orchestrator turn can apply the explicit
# PDF fallback or Office/Image fail-fast policy.
tools_node = ToolNode(ORCHESTRATOR_TOOLS, handle_tool_errors=True)


def build_ingestion_graph(llm: BaseChatModel | None = None):
    """Build and compile the LLM → ToolNode → LLM review loop.

    ``llm`` is an optional dependency-injection seam for deterministic tests
    and local model adapters. In production, omitting it makes the node use
    the configured provider from ``src.core.llm_client``.
    """
    workflow = StateGraph(AgentState)

    def run_orchestrator(state: AgentState) -> dict[str, Any]:
        return orchestrator_node(state, llm=llm)

    workflow.add_node("orchestrator_node", run_orchestrator)
    workflow.add_node("tools_node", tools_node)
    workflow.add_edge(START, "orchestrator_node")
    workflow.add_conditional_edges(
        "orchestrator_node",
        _route_after_orchestrator,
        {"tools": "tools_node", "end": END},
    )
    workflow.add_edge("tools_node", "orchestrator_node")
    return workflow.compile()


def run_ingestion(
    raw_content: str = "",
    file_path: str | None = None,
    output_path: str | None = None,
    llm: BaseChatModel | None = None,
) -> AgentState:
    """Run the LLM-driven ingestion loop and return its final state.

    Args:
        raw_content: Optional Markdown/text supplied directly by the caller.
        file_path: Optional local file path for file-based ingestion.
        output_path: Optional destination ending in ``.json``.
        llm: Optional injected chat model for tests or a local provider.
    """
    initial_state = create_initial_state(raw_content=raw_content, file_path=file_path)
    initial_state["output_path"] = output_path
    graph = build_ingestion_graph(llm=llm) if llm is not None else ingestion_graph
    result = graph.invoke(initial_state)
    logger.info(f"Ingestion graph completed at stage={result.get('stage')}")
    return cast(AgentState, result)


ingestion_graph = build_ingestion_graph()


__all__ = [
    "ORCHESTRATOR_TOOLS",
    "PARSER_TOOLS",
    "AgentState",
    "build_ingestion_graph",
    "ingestion_graph",
    "load_skill",
    "orchestrator_node",
    "run_ingestion",
    "tools_node",
]
