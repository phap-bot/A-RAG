"""Synthesizer Sub-Agent Node.

RULE 1: Shared State ONLY. Reads from and writes exclusively to AgentState.
RULE 2: No hardcoded prompts. Imports templates from src.core.prompts.
"""

from typing import Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.orchestrator.state import AgentState
from src.core.config import logger, settings
from src.core.llm_client import get_chat_llm
from src.core.prompts import (
    SYNTHESIZER_SYSTEM_PROMPT,
    SYNTHESIZER_USER_TEMPLATE,
)


def synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """Synthesize grounded answer strictly relying on retrieved chunks and knowledge graph context."""
    query = state["query"]
    retrieved_docs = state.get("retrieved_docs", [])
    graph_context = state.get("graph_context", [])

    logger.info(
        f"Entering Synthesizer: generating answer for query='{query}' using {len(retrieved_docs)} chunks"
    )

    # Format chunks context
    chunks_str_list = []
    for chunk in retrieved_docs:
        chunk_repr = f"[{chunk.chunk_id}] (Modality: {chunk.modality}, Source: {chunk.source_doc})\n{chunk.content}"
        if chunk.vlm_caption:
            chunk_repr += f"\n[VLM Description]: {chunk.vlm_caption}"
        chunks_str_list.append(chunk_repr)
    retrieved_context_str = "\n\n---\n\n".join(chunks_str_list) if chunks_str_list else "No retrieved chunks."

    # Format graph context
    graph_str = "\n".join(
        [
            f"- ({rel.get('source_node')}) -[{rel.get('relationship')}]-> ({rel.get('target_node')})"
            for rel in graph_context
        ]
    ) if graph_context else "No graph entities identified."

    formatted_user_prompt = SYNTHESIZER_USER_TEMPLATE.format(
        query=query,
        retrieved_context=retrieved_context_str,
        graph_context=graph_str,
    )

    # Call LLM or deterministic grounded fallback
    if settings.openai_api_key and settings.openai_api_key != "sk-mock-key-replace-with-actual":
        try:
            llm = get_chat_llm(temperature=0.1)
            messages = [
                SystemMessage(content=SYNTHESIZER_SYSTEM_PROMPT),
                HumanMessage(content=formatted_user_prompt),
            ]
            response = llm.invoke(messages)
            synthesized_text = response.content
        except Exception as exc:
            logger.warning(f"LLM synthesis failed: {exc}. Using deterministic grounded response.")
            synthesized_text = _fallback_synthesis(query, retrieved_docs, graph_context)
    else:
        synthesized_text = _fallback_synthesis(query, retrieved_docs, graph_context)

    logger.info("Synthesizer finished candidate answer generation")
    return {"synthesized_response": synthesized_text}


def _fallback_synthesis(query: str, chunks: list[Any], graph: list[dict]) -> str:
    """Deterministic grounded response adhering to citation constraints."""
    if not chunks:
        return f"Based on available documentation, there is insufficient context to answer: '{query}'."

    primary_chunk = chunks[0]
    citations = f"[Chunk: {primary_chunk.chunk_id}]"

    response_lines = [
        f"Regarding your query **{query}**:",
        f"{primary_chunk.content} {citations}",
    ]

    if len(chunks) > 1 and chunks[1].modality == "table":
        response_lines.append(f"\nStructured Details:\n{chunks[1].content} [Chunk: {chunks[1].chunk_id}]")

    if graph:
        response_lines.append(
            f"\nRelated Architecture Entities: `{graph[0].get('source_node')}` -> `{graph[0].get('target_node')}` [Graph: {graph[0].get('relationship')}]."
        )

    return "\n\n".join(response_lines)
