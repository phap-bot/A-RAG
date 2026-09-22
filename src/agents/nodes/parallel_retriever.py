"""Parallel Retrieval Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 3: MCP Isolation. Must NOT import database drivers directly. Calls MCP tools from src.mcp_server.tools.
"""

from typing import Any, Dict
from src.agents.orchestrator.state import AgentState
from src.core.config import logger
from src.mcp_server.tools.kb_retrieval_tools import (
    query_knowledge_graph,
    search_knowledge_base,
)


def parallel_retriever_node(state: AgentState) -> Dict[str, Any]:
    """Run the Retriever agent; retrieval itself is selected through ToolNode."""
    from src.agents.agent_runtime import run_agent_node

    logger.info("Entering Parallel Retriever agent")
    return run_agent_node("parallel_retriever", state)
