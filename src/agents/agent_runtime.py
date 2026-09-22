"""Runtime for the Main agent, specialist handoffs and shared ToolNode."""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from src.agents.nodes.provenance import validate_response_provenance
from src.agents.orchestrator.state import AgentState, CriticVerdict, QueryFormulationOutput, RetrievedChunk
from src.agents.tools.registry import build_agent_tools
from src.core.config import logger, settings
from src.core.llm_client import get_chat_llm
from src.core.prompts import AGENT_SYSTEM_PROMPTS


_AGENTS = (
    "agent_main",
    "query_formulator",
    "parallel_retriever",
    "synthesizer",
    "critic_reflection",
)


def messages_key(agent: str) -> str:
    return f"{agent}_messages"


def _llm_is_configured() -> bool:
    if not settings.agent_llm_enabled:
        return False
    key = (settings.openai_api_key or "").strip()
    return key not in {
        "",
        "sk-mock-key-replace-with-actual",
        "sk-mock-placeholder-key",
        "[REDACTED:openai-key]",
    }


def _message_text(value: Any) -> str:
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def _parse_json(text: str) -> Any:
    candidates = [text.strip()]
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        candidates.append("\n".join(lines[1:-1]).strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        # Tool results are frequently JSON arrays (search and graph results),
        # while model turns are usually objects. Preserve both shapes so the
        # shared ToolNode effect projector can normalize every result.
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _compact_context(state: AgentState, *, content_limit: int = 1400) -> dict[str, Any]:
    critique = state.get("critique")
    best = state.get("best_candidate", {})
    return {
        "question": state.get("query", ""),
        "conversation_history": state.get("history", [])[-6:],
        "selected_file_paths": state.get("file_paths", []),
        "current_attempt": int(state.get("retry_count", 0)) + 1,
        "maximum_attempts": state.get("max_retries", 3),
        "handoff_reason": state.get("handoff_reason", ""),
        "formulated_queries": state.get("formulated_queries", []),
        "graph_queries": state.get("graph_queries", []),
        "evidence": [
            {
                "chunk_id": chunk.chunk_id,
                "source": chunk.source_doc,
                "score": chunk.score,
                "section": chunk.section_path,
                "content": chunk.content[:content_limit],
            }
            for chunk in state.get("retrieved_docs", [])[: settings.retrieval_top_k]
        ],
        "graph_context": state.get("graph_context", [])[: settings.retrieval_top_k],
        "candidate_answer": state.get("synthesized_response") or "",
        "previous_critique": critique.model_dump(mode="json") if critique else None,
        "best_candidate_summary": {
            "score": best.get("score"),
            "passed": best.get("passed"),
            "answer": str(best.get("answer", ""))[:content_limit],
        } if best else None,
        "tool_trace": state.get("retrieval_trace", {}).get("tool_calls", [])[-12:],
        "errors": state.get("errors", [])[-8:],
    }


def _task_message(agent: str, state: AgentState) -> str:
    context = _compact_context(state)
    instructions = {
        "agent_main": (
            "Coordinate the specialist agents through handoff tools. Decide which agent or knowledge tool "
            "is needed from the current state. Issue at most one specialist handoff in a turn. "
            "Ensure a newly drafted answer is reviewed by the Critic. "
            "Do not invent a completed review or claim evidence that is absent."
        ),
        "query_formulator": (
            "Produce a compact query plan as JSON with original_query, intent, hybrid_search_queries, "
            "graph_entity_queries and reformulation_rationale. Use retrieval tools when a probe would "
            "materially improve the search plan."
        ),
        "parallel_retriever": (
            "Find relevant evidence by calling the search and graph tools. Use more than one focused "
            "query when useful, inspect exact evidence when needed, and finish only after reporting what "
            "was found or why retrieval failed."
        ),
        "synthesizer": (
            "Write a complete answer using only retrieved evidence. Call evidence/search tools if a "
            "citation needs inspection or a specific gap remains. Cite every material claim as "
            "[Chunk: chunk_id] or [Graph: relationship-or-node]."
        ),
        "critic_reflection": (
            "Independently check the candidate against source evidence and citations. Use get_evidence or "
            "targeted retrieval when needed. Return JSON matching CriticVerdict: passed, faithfulness_score, "
            "relevance_score, citation_accuracy_score, critique_feedback, should_reformulate and "
            "suggested_query_refinements."
        ),
    }[agent]
    return f"{instructions}\n\nShared run context:\n{json.dumps(context, ensure_ascii=False, default=str)}"


def _fallback_handoff(target: str, reason: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": f"handoff_to_{target}",
            "args": {"reason": reason},
            "id": f"call_{uuid4().hex[:12]}",
            "type": "tool_call",
        }],
    )


def _fallback_tool_calls(calls: list[tuple[str, dict[str, Any]]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{uuid4().hex[:12]}", "type": "tool_call"}
            for name, args in calls
        ],
    )


def _fallback_agent_response(agent: str, state: AgentState) -> AIMessage:
    """Offline fallback; it still emits tool calls for ToolNode to execute."""
    if agent == "agent_main":
        critique = state.get("critique")
        count = int(state.get("retry_count", 0))
        maximum = int(state.get("max_retries", 3))
        if int(state.get("candidate_version", 0)) > int(state.get("critique_version", 0)):
            return _fallback_handoff("critic_reflection", "Review the latest candidate before returning it.")
        if critique and not critique.passed and count < maximum:
            target = "query_formulator" if critique.should_reformulate else "synthesizer"
            return _fallback_handoff(target, critique.critique_feedback)
        if not state.get("formulation_count", 0):
            return _fallback_handoff("query_formulator", "Create focused retrieval queries.")
        if not state.get("retrieval_complete"):
            return _fallback_handoff("parallel_retriever", "Collect workspace evidence for the query.")
        if not state.get("synthesized_response"):
            return _fallback_handoff("synthesizer", "Draft a cited answer from current evidence.")
        return AIMessage(content="The reviewed answer is ready.")

    if agent == "query_formulator":
        from src.agents.nodes.query_formulator import _fallback_formulation

        formulated, graph_queries = _fallback_formulation(state["query"], state.get("critique"))
        return AIMessage(content=json.dumps({
            "original_query": state["query"],
            "intent": "Answer from workspace knowledge",
            "hybrid_search_queries": formulated,
            "graph_entity_queries": graph_queries,
            "reformulation_rationale": "Deterministic local fallback",
        }, ensure_ascii=False))

    if agent == "parallel_retriever":
        messages = list(state.get(messages_key(agent), []))
        if messages and isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Retrieval complete; evidence is available in shared state.")
        calls: list[tuple[str, dict[str, Any]]] = []
        for query in list(dict.fromkeys(state.get("formulated_queries", [state["query"]])))[:4]:
            calls.append(("search_knowledge_base", {"query": query, "limit": settings.retrieval_top_k}))
        for query in list(dict.fromkeys(state.get("graph_queries", [])))[:4]:
            calls.append(("query_knowledge_graph", {"entity_query": query, "max_hops": settings.neo4j_graph_max_hops}))
        if calls:
            return _fallback_tool_calls(calls)
        return AIMessage(content="No search query was available.")

    if agent == "synthesizer":
        from src.agents.nodes.synthesizer import _fallback_synthesis

        return AIMessage(content=_fallback_synthesis(
            state["query"], state.get("retrieved_docs", []), state.get("graph_context", [])
        ))

    if agent == "critic_reflection":
        from src.agents.nodes.critic_reflection import _deterministic_evaluation

        verdict = _deterministic_evaluation(
            state.get("synthesized_response", ""),
            state.get("retrieved_docs", []),
            max(0, int(state.get("retry_count", 0))),
            int(state.get("max_retries", 3)),
        )
        return AIMessage(content=verdict.model_dump_json())

    return AIMessage(content="")


def _final_candidate_updates(state: AgentState) -> dict[str, Any]:
    best = state.get("best_candidate", {})
    if not best:
        return {
            "synthesized_response": state.get("synthesized_response") or (
                f"Insufficient verified context to answer: {state.get('query', '')}"
            ),
            "run_status": "completed_best_effort",
        }
    critique = best.get("critique")
    return {
        "synthesized_response": best.get("answer", ""),
        "retrieved_docs": best.get("retrieved_docs", []),
        "graph_context": best.get("graph_context", []),
        "retrieval_trace": best.get("retrieval_trace", state.get("retrieval_trace", {})),
        "critique": CriticVerdict.model_validate(critique) if critique else state.get("critique"),
        "provenance_validation": best.get("provenance_validation", {}),
        "run_status": "completed" if best.get("passed") else "completed_best_effort",
    }


def run_agent_node(agent: str, state: AgentState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """Run one tool-aware agent turn and project its final result into state."""
    role_key = messages_key(agent)
    transcript = list(state.get(role_key, []))

    terminal = (
        agent == "agent_main"
        and state.get("critique") is not None
        and int(state.get("critique_version", 0)) >= int(state.get("candidate_version", 0))
        and (
            bool(state["critique"].passed)
            or int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3))
        )
    )
    if terminal:
        return {**_final_candidate_updates(state), "active_agent": agent}

    if not transcript:
        transcript.append(SystemMessage(content=AGENT_SYSTEM_PROMPTS[agent]))
    # Returning from ToolNode means the tool result is the latest message;
    # invoke the same agent on that transcript without duplicating its request.
    resuming_tool_turn = (
        transcript
        and isinstance(transcript[-1], ToolMessage)
        and not str(getattr(transcript[-1], "name", "")).startswith("handoff_to_")
        and str(state.get("active_agent", agent)) == agent
    )
    if not resuming_tool_turn:
        transcript.append(HumanMessage(content=_task_message(agent, state)))

    tools = build_agent_tools(agent)
    response: AIMessage
    if _llm_is_configured():
        try:
            llm = get_chat_llm(
                temperature=0.0 if agent in {"agent_main", "query_formulator", "critic_reflection"} else 0.1
            ).bind_tools(tools)
            response = llm.invoke(transcript, config=config)
        except Exception as exc:
            logger.warning(f"Agent {agent} LLM turn failed; using tool-aware fallback: {exc}")
            response = _fallback_agent_response(agent, state)
    else:
        response = _fallback_agent_response(agent, state)

    transcript.append(response)
    update: dict[str, Any] = {role_key: transcript, "active_agent": agent}
    if getattr(response, "tool_calls", None):
        return update

    text = _message_text(response)
    update.update(_complete_agent_turn(agent, state, text))
    return update


def _complete_agent_turn(agent: str, state: AgentState, text: str) -> dict[str, Any]:
    if agent == "agent_main":
        if state.get("critique") and (
            state["critique"].passed
            or int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3))
        ):
            return _final_candidate_updates(state)
        # A free-text Main response cannot bypass the quality gate. Routing
        # below will send it to the next missing stage or back to a specialist.
        return {"main_summary": text}

    if agent == "query_formulator":
        parsed = _parse_json(text)
        try:
            output = QueryFormulationOutput.model_validate(parsed or {})
            queries = output.hybrid_search_queries or [state["query"]]
            graph_queries = output.graph_entity_queries
        except Exception as exc:
            logger.warning(f"Formulator output was not valid JSON; using deterministic plan: {exc}")
            from src.agents.nodes.query_formulator import _fallback_formulation

            queries, graph_queries = _fallback_formulation(state["query"], state.get("critique"))
        return {
            "formulated_queries": list(dict.fromkeys(queries)),
            "graph_queries": list(dict.fromkeys(graph_queries)),
            "formulation_count": int(state.get("formulation_count", 0)) + 1,
            "retrieval_complete": False,
            "retrieved_docs": [],
            "graph_context": [],
            "synthesized_response": None,
            # The failed verdict remains in ``attempt_history``. Clearing the
            # active verdict is necessary so Main can execute the next
            # retrieve/synthesize attempt instead of handing the same failed
            # query back to Formulator forever.
            "critique": None,
            "critique_version": int(state.get("candidate_version", 0)),
            "active_agent": agent,
            "handoff_reason": "",
        }

    if agent == "parallel_retriever":
        return {"retrieval_complete": True, "active_agent": agent, "handoff_reason": ""}

    if agent == "synthesizer":
        if not text.strip():
            from src.agents.nodes.synthesizer import _fallback_synthesis

            text = _fallback_synthesis(state["query"], state.get("retrieved_docs", []), state.get("graph_context", []))
        return {
            "synthesized_response": text,
            "candidate_version": int(state.get("candidate_version", 0)) + 1,
            "active_agent": agent,
            "handoff_reason": "",
        }

    if agent == "critic_reflection":
        parsed = _parse_json(text)
        try:
            verdict = CriticVerdict.model_validate(parsed or {})
        except Exception as exc:
            logger.warning(f"Critic output was not valid JSON; using deterministic validation: {exc}")
            from src.agents.nodes.critic_reflection import _deterministic_evaluation

            verdict = _deterministic_evaluation(
                state.get("synthesized_response", ""), state.get("retrieved_docs", []),
                int(state.get("retry_count", 0)), int(state.get("max_retries", 3)),
            )

        provenance = validate_response_provenance(
            state.get("synthesized_response", ""), state.get("retrieved_docs", []), state.get("graph_context", [])
        )
        has_reference = bool(provenance["cited_chunk_ids"] or provenance["cited_graph_ids"])
        if not provenance["valid"] or (state.get("retrieved_docs") and not has_reference):
            verdict = verdict.model_copy(update={
                "passed": False,
                "citation_accuracy_score": 0.0,
                "should_reformulate": True,
                "critique_feedback": (
                    "Provenance gate rejected candidate: "
                    f"unknown_chunks={provenance['unknown_chunk_ids']}, "
                    f"unknown_graph={provenance['unknown_graph_ids']}"
                ),
            })

        attempt = int(state.get("retry_count", 0)) + 1
        score = (
            0.5 * verdict.faithfulness_score
            + 0.3 * verdict.relevance_score
            + 0.2 * verdict.citation_accuracy_score
        )
        candidate = {
            "answer": state.get("synthesized_response", ""),
            "retrieved_docs": list(state.get("retrieved_docs", [])),
            "graph_context": list(state.get("graph_context", [])),
            "retrieval_trace": dict(state.get("retrieval_trace", {})),
            "critique": verdict.model_dump(mode="json"),
            "provenance_validation": provenance,
            "score": score,
            "passed": verdict.passed,
            "attempt": attempt,
        }
        best = state.get("best_candidate", {})
        replace_best = (
            not best
            or (candidate["passed"] and not best.get("passed"))
            or (candidate["passed"] == bool(best.get("passed")) and score > float(best.get("score", -1.0)))
        )
        history = list(state.get("attempt_history", []))
        history.append({
            "attempt": attempt,
            "passed": verdict.passed,
            "score": score,
            "faithfulness_score": verdict.faithfulness_score,
            "relevance_score": verdict.relevance_score,
            "citation_accuracy_score": verdict.citation_accuracy_score,
            "critique_feedback": verdict.critique_feedback,
            "provenance_valid": provenance["valid"],
        })
        return {
            "critique": verdict,
            "retry_count": attempt,
            "critique_version": int(state.get("candidate_version", 0)),
            "provenance_validation": provenance,
            "attempt_history": history,
            "best_candidate": candidate if replace_best else best,
            "active_agent": agent,
            "handoff_reason": "",
        }
    return {}


def _append_tool_effects(state: AgentState, active_agent: str, new_messages: list[Any]) -> dict[str, Any]:
    retrieved = list(state.get("retrieved_docs", []))
    chunk_by_id = {chunk.chunk_id: chunk for chunk in retrieved}
    graph = list(state.get("graph_context", []))
    graph_keys = {
        (str(item.get("source_node")), str(item.get("relationship")), str(item.get("target_node")))
        for item in graph
    }
    trace = dict(state.get("retrieval_trace", {}))
    calls = list(trace.get("tool_calls", []))
    errors = list(state.get("errors", []))
    active = active_agent
    handoff_reason = state.get("handoff_reason", "")
    agent_handoffs = int(state.get("agent_handoffs", 0))
    request_messages = list(state.get(messages_key(active_agent), []))
    last_ai = next((message for message in reversed(request_messages) if isinstance(message, AIMessage)), None)
    tool_calls = {call.get("id"): call for call in (getattr(last_ai, "tool_calls", []) or [])}
    trace.setdefault("vector_queries", [])
    trace.setdefault("graph_queries", [])

    for message in new_messages:
        if not isinstance(message, ToolMessage):
            continue
        call = tool_calls.get(getattr(message, "tool_call_id", None), {})
        args = call.get("args", {}) if isinstance(call, dict) else {}
        tool_name = str(getattr(message, "name", ""))
        content = _message_text(message)
        parsed = _parse_json(content)
        if isinstance(parsed, dict) and parsed.get("handoff_to"):
            target = str(parsed["handoff_to"])
            if target in _AGENTS:
                active = target
                handoff_reason = str(parsed.get("reason", ""))
                agent_handoffs += 1
        elif getattr(message, "status", "") == "error":
            errors.append(f"{tool_name} failed: {content}")
        else:
            value: Any = parsed if parsed is not None else None
            if value is None:
                try:
                    value = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    value = content
            if tool_name == "search_knowledge_base":
                items = value if isinstance(value, list) else []
                for item in items:
                    try:
                        chunk = RetrievedChunk.model_validate(item)
                    except Exception:
                        continue
                    chunk_by_id.setdefault(chunk.chunk_id, chunk)
                query = str(args.get("query", ""))
                trace["vector_queries"].append({"query": query, "result_count": len(items)})
            elif tool_name == "query_knowledge_graph":
                items = value if isinstance(value, list) else []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    key = (str(item.get("source_node")), str(item.get("relationship")), str(item.get("target_node")))
                    if key not in graph_keys:
                        graph_keys.add(key)
                        graph.append(item)
                trace["graph_queries"].append({
                    "query": str(args.get("entity_query", "")), "result_count": len(items)
                })
            elif tool_name == "get_evidence" and isinstance(value, dict):
                try:
                    chunk = RetrievedChunk.model_validate(value)
                    chunk_by_id.setdefault(chunk.chunk_id, chunk)
                except Exception:
                    errors.append(f"get_evidence returned an invalid chunk: {value.get('chunk_id', '')}")

        calls.append({
            "agent": active_agent,
            "tool": tool_name,
            "arguments": args,
            "status": "error" if getattr(message, "status", "") == "error" else "completed",
        })

    retrieved = sorted(chunk_by_id.values(), key=lambda item: item.score, reverse=True)[: settings.retrieval_top_k]
    graph.sort(key=lambda item: (
        str(item.get("source_node", "")), str(item.get("relationship", "")), str(item.get("target_node", ""))
    ))
    trace["tool_calls"] = calls
    trace["workspace_id"] = state.get("workspace_id", "")
    trace["vector_queries"] = trace["vector_queries"][-32:]
    trace["graph_queries"] = trace["graph_queries"][-32:]
    return {
        "retrieved_docs": retrieved,
        "graph_context": graph[: settings.retrieval_top_k],
        "retrieval_trace": trace,
        "errors": errors[-32:],
        "active_agent": active,
        "handoff_reason": handoff_reason,
        "agent_handoffs": agent_handoffs,
    }


def execute_agent_tools(state: AgentState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """Shared graph tools node; dispatches only the active agent's allowlist."""
    active = str(state.get("active_agent", "agent_main"))
    key = messages_key(active)
    transcript = list(state.get(key, []))
    last_ai = next((message for message in reversed(transcript) if isinstance(message, AIMessage)), None)
    pending = getattr(last_ai, "tool_calls", []) or []
    rounds = dict(state.get("agent_tool_rounds", {}))
    current_rounds = int(rounds.get(active, 0))
    if current_rounds >= int(state.get("max_tool_rounds", 6)):
        tool_messages = [
            ToolMessage(
                content="Tool call round limit reached for this agent.",
                tool_call_id=str(call.get("id", "")),
                name=str(call.get("name", "tool")),
                status="error",
            )
            for call in pending
        ]
        result: dict[str, Any] = {key: tool_messages}
    else:
        node = ToolNode(build_agent_tools(active), handle_tool_errors=True, messages_key=key)
        result = node.invoke(state, config=config)
        rounds[active] = current_rounds + 1
        result["agent_tool_rounds"] = rounds

    new_messages = result.get(key, [])
    effects = _append_tool_effects(state, active, new_messages)
    # ``max_tool_rounds`` limits a single agent's consecutive data-tool loop.
    # A handoff starts a fresh turn for the receiving agent, so a supervisor
    # must not consume its entire round budget merely by coordinating several
    # specialists across one bounded run. Total delegation remains capped by
    # ``max_agent_handoffs``.
    if effects.get("active_agent") != active:
        rounds[active] = 0
        result["agent_tool_rounds"] = rounds
    handoff_limit = int(state.get("max_agent_handoffs", 12))
    if int(effects["agent_handoffs"]) > handoff_limit:
        effects["active_agent"] = "agent_main"
        effects["handoff_reason"] = "Agent handoff limit reached; Main must finalize the best available result."
        errors = list(effects.get("errors", []))
        errors.append("Maximum agent handoffs reached")
        effects["errors"] = errors[-32:]
    result.update(effects)
    return result


def route_after_agent(agent: str, state: AgentState) -> str:
    """Conditional edge from an agent: tools, a specialist handoff, or final."""
    transcript = list(state.get(messages_key(agent), []))
    last = transcript[-1] if transcript else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"

    if agent != "agent_main":
        return "agent_main"

    if state.get("run_status") in {"completed", "completed_best_effort"}:
        return "end"
    candidate_version = int(state.get("candidate_version", 0))
    critique_version = int(state.get("critique_version", 0))
    if candidate_version > critique_version:
        return "critic_reflection"

    critique = state.get("critique")
    if critique and critique.passed:
        return "end"
    if critique and int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3)):
        return "end"

    # A failed Critic can request a reformulation; otherwise Main is free to
    # hand work to any specialist. These defaults only recover from a model
    # that returned prose rather than a handoff tool call.
    if critique and not critique.passed:
        if critique.should_reformulate:
            return "query_formulator"
        return "synthesizer" if state.get("retrieval_complete") else "parallel_retriever"
    if not int(state.get("formulation_count", 0)):
        return "query_formulator"
    if not state.get("retrieval_complete"):
        return "parallel_retriever"
    if not state.get("synthesized_response"):
        return "synthesizer"
    return "critic_reflection"


def route_after_tools(state: AgentState) -> str:
    """Return to the active agent after a data tool, or follow a handoff."""
    active = str(state.get("active_agent", "agent_main"))
    return active if active in _AGENTS else "agent_main"


def route_to_end() -> Any:
    return END


__all__ = [
    "execute_agent_tools",
    "messages_key",
    "route_after_agent",
    "route_after_tools",
    "run_agent_node",
]
