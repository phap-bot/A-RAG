"""Role-scoped LangChain tools for the Agentic RAG team.

The graph agents see tool schemas and choose when to call them. This module
owns the tool catalog and adapters; agent nodes must not import or invoke
retrieval functions directly. The same domain functions are also exposed by
the network MCP server.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from src.agents.orchestrator.state import AgentState
from src.core.config import settings


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@tool("search_knowledge_base")
def search_knowledge_base_tool(
    query: str,
    limit: int = 5,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Search this request's workspace using hybrid dense and full-text retrieval.

    The authenticated workspace and selected file scope are injected by the
    graph runtime and are never model-controlled arguments.
    """
    from src.agents.nodes import parallel_retriever

    if state is None:
        raise ValueError("Injected graph state is required for retrieval")
    bounded_limit = max(1, min(int(limit), settings.retrieval_top_k))
    file_paths = list(state.get("file_paths", []))
    result = parallel_retriever.search_knowledge_base(
        query=query,
        limit=bounded_limit,
        filter_metadata={"file_paths": file_paths},
        workspace_id=state["workspace_id"],
    )
    return _json(result)


@tool("query_knowledge_graph")
def query_knowledge_graph_tool(
    entity_query: str,
    max_hops: int = 2,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Expand bounded provenance and section relationships in this workspace."""
    from src.agents.nodes import parallel_retriever

    if state is None:
        raise ValueError("Injected graph state is required for graph retrieval")
    bounded_hops = max(1, min(int(max_hops), settings.neo4j_graph_max_hops))
    query_args: dict[str, Any] = {
        "entity_query": entity_query,
        "max_hops": bounded_hops,
        "workspace_id": state["workspace_id"],
    }
    if state.get("file_paths"):
        query_args["file_paths"] = list(state["file_paths"])
    result = parallel_retriever.query_knowledge_graph(**query_args)
    return _json(result)


@tool("get_evidence")
def get_evidence_tool(
    chunk_id: str,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Fetch the exact source chunk and lineage for a citation or fact check."""
    from src.mcp_server.tools.kb_retrieval_tools import get_evidence

    if state is None:
        raise ValueError("Injected graph state is required for evidence lookup")
    return _json(get_evidence(workspace_id=state["workspace_id"], chunk_id=chunk_id))


def _handoff(target: str, reason: str) -> str:
    return _json({"handoff_to": target, "reason": reason})


@tool("handoff_to_query_formulator")
def handoff_to_query_formulator(reason: str) -> str:
    """Delegate query decomposition or a targeted reformulation to the Formulator agent."""
    return _handoff("query_formulator", reason)


@tool("handoff_to_parallel_retriever")
def handoff_to_parallel_retriever(reason: str) -> str:
    """Delegate evidence discovery to the Retriever agent."""
    return _handoff("parallel_retriever", reason)


@tool("handoff_to_synthesizer")
def handoff_to_synthesizer(reason: str) -> str:
    """Delegate a grounded answer draft to the Synthesizer agent."""
    return _handoff("synthesizer", reason)


@tool("handoff_to_critic_reflection")
def handoff_to_critic_reflection(reason: str) -> str:
    """Ask the Critic agent to validate the current answer and its sources."""
    return _handoff("critic_reflection", reason)


@tool("handoff_to_main")
def handoff_to_main(reason: str) -> str:
    """Return a specialist's findings or a request for help to the Main agent."""
    return _handoff("agent_main", reason)


_DATA_TOOLS: tuple[BaseTool, ...] = (
    search_knowledge_base_tool,
    query_knowledge_graph_tool,
    get_evidence_tool,
)

_HANDOFF_TOOLS: dict[str, BaseTool] = {
    "query_formulator": handoff_to_query_formulator,
    "parallel_retriever": handoff_to_parallel_retriever,
    "synthesizer": handoff_to_synthesizer,
    "critic_reflection": handoff_to_critic_reflection,
    "agent_main": handoff_to_main,
}

_ROLE_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    # The Main agent can use evidence directly or delegate a bounded piece of
    # work to a specialist. It remains the only node that owns task routing.
    "agent_main": (
        "search_knowledge_base", "query_knowledge_graph", "get_evidence",
        "handoff_to_query_formulator", "handoff_to_parallel_retriever",
        "handoff_to_synthesizer", "handoff_to_critic_reflection",
    ),
    "query_formulator": (
        "search_knowledge_base", "query_knowledge_graph", "get_evidence",
        "handoff_to_main", "handoff_to_parallel_retriever",
    ),
    "parallel_retriever": (
        "search_knowledge_base", "query_knowledge_graph", "get_evidence",
        "handoff_to_main", "handoff_to_query_formulator",
    ),
    "synthesizer": (
        "search_knowledge_base", "query_knowledge_graph", "get_evidence",
        "handoff_to_main", "handoff_to_parallel_retriever",
    ),
    "critic_reflection": (
        "search_knowledge_base", "query_knowledge_graph", "get_evidence",
        "handoff_to_main", "handoff_to_parallel_retriever",
        "handoff_to_synthesizer",
    ),
}


def build_agent_tools(agent: str) -> list[BaseTool]:
    """Return the role-specific tool allowlist advertised to one agent."""
    by_name = {tool_obj.name: tool_obj for tool_obj in (*_DATA_TOOLS, *_HANDOFF_TOOLS.values())}
    try:
        return [by_name[name] for name in _ROLE_TOOL_NAMES[agent]]
    except KeyError as exc:
        raise ValueError(f"Unknown Agentic RAG role: {agent}") from exc


def handoff_target(tool_name: str) -> str | None:
    """Resolve a completed handoff tool call to its LangGraph node name."""
    for target, handoff_tool in _HANDOFF_TOOLS.items():
        if handoff_tool.name == tool_name:
            return target
    return None


__all__ = ["build_agent_tools", "handoff_target"]
