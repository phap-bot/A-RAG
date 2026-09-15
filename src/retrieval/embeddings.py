"""Configurable embedding boundary used by the Neo4j vector repository.

The repository does not manufacture vectors.  This module owns the provider
choice and exposes a small protocol so tests and future local embedding
providers can be injected without changing Neo4j or agent code.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock
from typing import Any, Protocol

from src.core.config import settings
from src.core.exceptions import ConfigurationError
from src.core.llm_client import get_embedding_model


class EmbeddingProvider(Protocol):
    """Minimal provider contract required by indexing and query retrieval."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per input text in input order."""

    def embed_query(self, text: str) -> list[float]:
        """Return one embedding for a query string."""


class OpenAIEmbeddingProvider:
    """Lazy adapter around the configured LangChain OpenAI embedding client."""

    def __init__(self) -> None:
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = get_embedding_model()
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = []
        batch_size = settings.embedding_batch_size
        for start in range(0, len(texts), batch_size):
            vectors.extend(self.model.embed_documents(list(texts[start : start + batch_size])))
        _validate_dimensions(vectors)
        return [list(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.embed_query(text)
        _validate_dimensions([vector])
        return list(vector)


class LocalBGEEmbeddingProvider:
    """Generate dense multilingual embeddings with the local BGE-M3 model.

    BGE-M3 can expose dense, sparse and multi-vector representations.  This
    adapter deliberately selects its dense representation because Neo4j's
    configured vector index stores one fixed-size vector per Chunk.  Lexical
    matching remains the separate BM25 channel in the hybrid retriever.
    """

    def __init__(self, model: Any | None = None) -> None:
        self._model = model
        self._lock = RLock()

    @property
    def model(self) -> Any:
        """Load the local model on first use so API startup stays lightweight."""
        with self._lock:
            if self._model is not None:
                return self._model

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ConfigurationError(
                    "Local BGE-M3 embeddings require sentence-transformers",
                    details={"install": "pip install sentence-transformers"},
                ) from exc

            model_options: dict[str, Any] = {}
            if settings.embedding_device != "auto":
                model_options["device"] = settings.embedding_device
            if settings.embedding_cache_dir:
                model_options["cache_folder"] = settings.embedding_cache_dir

            try:
                self._model = SentenceTransformer(
                    settings.embedding_model_name,
                    **model_options,
                )
            except Exception as exc:
                raise ConfigurationError(
                    "Failed to load the local BGE-M3 embedding model",
                    details={
                        "model": settings.embedding_model_name,
                        "device": settings.embedding_device,
                        "cache_dir": settings.embedding_cache_dir,
                        "error": str(exc),
                    },
                ) from exc
            return self._model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode texts into normalized dense vectors and validate dimensions."""
        if not texts:
            return []

        with self._lock:
            encoded = self.model.encode(
                list(texts),
                batch_size=settings.embedding_batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=settings.embedding_normalize,
            )

        if len(texts) == 1 and getattr(encoded, "ndim", 2) == 1:
            encoded = [encoded]
        vectors = [
            [float(value) for value in vector]
            for vector in encoded
        ]
        _validate_dimensions(vectors)
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode document chunks in input order for deterministic upsert mapping."""
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        """Encode one user query with the same dense space as indexed chunks."""
        return self._encode([text])[0]


def _validate_dimensions(vectors: Sequence[Sequence[float]]) -> None:
    expected = settings.embedding_dimension
    for vector in vectors:
        if len(vector) != expected:
            raise ConfigurationError(
                "Embedding dimension does not match Neo4j vector index",
                details={"expected": expected, "actual": len(vector)},
            )


def build_embedding_provider() -> EmbeddingProvider | None:
    """Build the configured provider, or return ``None`` when vectors are disabled.

    ``None`` is intentional: full-text retrieval remains available without an
    embedding credential, while vector retrieval reports that it is not ready.
    No deterministic pseudo-vector is generated because that would produce
    misleading relevance scores.
    """
    if not settings.embedding_enabled or settings.embedding_provider == "none":
        return None
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key or settings.openai_api_key == "sk-mock-key-replace-with-actual":
            raise ConfigurationError(
                "EMBEDDING_ENABLED=true requires a real OPENAI_API_KEY",
                details={"embedding_provider": settings.embedding_provider},
            )
        return OpenAIEmbeddingProvider()
    if settings.embedding_provider == "bge_m3":
        return LocalBGEEmbeddingProvider()
    raise ConfigurationError(
        f"Unsupported embedding provider: {settings.embedding_provider}",
        details={"supported": ["bge_m3", "openai", "none"]},
    )


__all__ = [
    "EmbeddingProvider",
    "LocalBGEEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "build_embedding_provider",
]
