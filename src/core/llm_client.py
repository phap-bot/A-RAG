"""LLM Client Factory & Provider Abstraction for Enterprise Agentic RAG."""

from typing import Any, Optional
from langchain_core.language_models.chat_models import BaseChatModel
try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except ImportError:
    ChatOpenAI = None  # type: ignore
    OpenAIEmbeddings = None  # type: ignore

from src.core.config import logger, settings
from src.core.exceptions import ConfigurationError


def get_chat_llm(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Instantiate a Chat LLM instance with configured credentials and error handling."""
    resolved_model = model_name or settings.primary_llm_model
    logger.debug(f"Initializing Chat LLM: model={resolved_model}, temperature={temperature}")

    if ChatOpenAI is None:
        raise ConfigurationError("langchain_openai package is not installed. Please install langchain-openai.")

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is not set. Utilizing mock/local client configuration.")

    try:
        return ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            api_key=settings.openai_api_key or "sk-mock-placeholder-key",
            base_url=settings.openai_base_url,
            max_tokens=max_tokens,
            **kwargs,
        )
    except Exception as exc:
        logger.error(f"Failed to instantiate Chat LLM '{resolved_model}': {exc}")
        raise ConfigurationError(
            f"Failed to initialize Chat LLM with model {resolved_model}",
            details={"error": str(exc)},
        ) from exc


def get_vlm_llm(
    temperature: float = 0.1,
    max_tokens: Optional[int] = 2048,
    **kwargs: Any,
) -> BaseChatModel:
    """Instantiate a Vision-Language Model (VLM) for multimodal layout and image captioning."""
    return get_chat_llm(
        model_name=settings.vlm_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


def get_embedding_model():
    """Instantiate text embedding model based on application settings."""
    logger.debug(f"Initializing Embedding model: {settings.embedding_model_name}")
    if OpenAIEmbeddings is None:
        raise ConfigurationError("langchain_openai package is not installed. Please install langchain-openai.")
    try:
        return OpenAIEmbeddings(
            model=settings.embedding_model_name,
            api_key=settings.openai_api_key or "sk-mock-placeholder-key",
            base_url=settings.openai_base_url,
        )
    except Exception as exc:
        logger.error(f"Failed to instantiate embedding model: {exc}")
        raise ConfigurationError(
            f"Failed to initialize embeddings with model {settings.embedding_model_name}",
            details={"error": str(exc)},
        ) from exc


__all__ = ["get_chat_llm", "get_vlm_llm", "get_embedding_model"]
