"""System configuration and structured JSON logging setup using Pydantic Settings and Loguru."""

import sys
from typing import Literal, Optional
from loguru import logger
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Enterprise Knowledge BaaS Configuration Settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application Settings
    app_name: str = Field(default="Enterprise-Agentic-RAG-BaaS", description="Application Name")
    app_env: Literal["development", "staging", "production", "test"] = Field(
        default="development", description="Environment stage"
    )
    debug: bool = Field(default=False, description="Debug mode flag")
    log_level: str = Field(default="INFO", description="Logging level")
    log_format_json: bool = Field(default=True, description="Enforce JSON structured logging for MCP")
    session_secret: str = Field(
        default="a-rag-development-session-key-change-before-production",
        description="Secret used to sign the browser session cookie",
    )
    session_cookie_secure: bool = Field(
        default=False,
        description="Set Secure on the browser session cookie in HTTPS deployments",
    )

    # API Configuration
    api_host: str = Field(default="0.0.0.0", description="FastAPI host")
    api_port: int = Field(default=8010, description="FastAPI port; MinerU occupies 8000 by default")
    api_prefix: str = Field(default="/api/v1", description="FastAPI route prefix")
    cors_allow_origin_regex: str = Field(
        default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        description="Allowed local frontend origins for development CORS preflight",
    )

    # LLM & Embedding Settings
    openai_api_key: str = Field(default="", description="OpenAI API Key")
    openai_base_url: Optional[str] = Field(default="https://api.openai.com/v1", description="OpenAI API Base URL")
    primary_llm_model: str = Field(default="gpt-4o", description="Primary Chat LLM model")
    vlm_model_name: str = Field(default="gpt-4o", description="Vision-Language Model for multimodal ingestion")
    embedding_model_name: str = Field(
        default="BAAI/bge-m3", description="Local embedding model name"
    )
    embedding_dimension: int = Field(default=1024, description="Embedding vector dimension")

    # Agentic Reflection Loop
    max_reflection_retries: int = Field(
        default=3, ge=1, le=5, description="Maximum total candidate review attempts, including the initial answer"
    )
    max_agent_handoffs: int = Field(default=12, ge=1, le=64, description="Maximum specialist handoffs in one RAG run")
    max_tool_rounds_per_agent: int = Field(default=6, ge=1, le=20, description="Maximum ToolNode rounds for one agent in one RAG run")
    agent_llm_enabled: bool = Field(
        default=False,
        description=(
            "Allow Zone 2 agents to call the configured chat model. Keep false for "
            "offline/local deterministic runs; enable explicitly in a deployment."
        ),
    )

    # Vector Database Settings
    vector_db_provider: Literal["neo4j"] = Field(
        default="neo4j", description="Vector and graph storage provider"
    )

    # Knowledge Graph (Neo4j) Settings
    neo4j_uri: str = Field(default="bolt://localhost:7687", description="Neo4j bolt URI")
    neo4j_user: str = Field(default="neo4j", description="Neo4j username")
    neo4j_password: str = Field(default="password123", description="Neo4j password")
    neo4j_database: str = Field(default="neo4j", description="Neo4j database name")
    neo4j_vector_index: str = Field(default="chunk_embedding_bge_m3", description="Neo4j Chunk vector index")
    neo4j_fulltext_index: str = Field(default="chunk_content_fulltext", description="Neo4j Chunk full-text index")
    neo4j_vector_query_mode: Literal["search", "procedure"] = Field(
        default="search",
        description="Neo4j vector query syntax; use procedure for pre-2026.01 servers",
    )
    neo4j_enabled: bool = Field(default=False, description="Enable live Neo4j persistence and retrieval")
    neo4j_auto_schema: bool = Field(default=True, description="Create/check Neo4j constraints and indexes on startup")
    neo4j_connection_timeout_seconds: float = Field(default=10.0, ge=1.0, description="Neo4j connectivity timeout")
    neo4j_max_connection_pool_size: int = Field(default=50, ge=1, description="Neo4j driver connection pool size")
    neo4j_vector_oversampling: int = Field(default=5, ge=1, le=20, description="Vector candidates fetched before metadata filtering")
    neo4j_rrf_k: int = Field(default=60, ge=1, description="Reciprocal-rank fusion constant")
    retrieval_top_k: int = Field(default=5, ge=1, le=50, description="Maximum chunks returned to Zone 2")
    retrieval_parallel_workers: int = Field(default=4, ge=1, le=16, description="Parallel retrieval worker count")
    neo4j_graph_max_hops: int = Field(default=2, ge=1, le=5, description="Maximum graph expansion depth")

    # Chunking controls are shared by the web ingestion boundary and any
    # background worker. Keeping them in settings prevents a deployment from
    # silently changing the indexed context window in application code.
    chunk_max_tokens: int = Field(default=400, ge=16, description="Maximum estimated tokens per chunk")
    chunk_overlap_tokens: int = Field(default=40, ge=0, description="Trailing overlap for text chunks")

    # Embeddings are explicit because Neo4j can store text/full-text chunks
    # without a vector, but vector retrieval must never pretend a vector exists.
    embedding_enabled: bool = Field(default=False, description="Generate/store embeddings for Neo4j vector search")
    embedding_provider: Literal["bge_m3", "openai", "none"] = Field(
        default="bge_m3", description="Embedding provider"
    )
    embedding_batch_size: int = Field(default=64, ge=1, le=512, description="Embedding request batch size")
    embedding_device: Literal["auto", "cpu", "cuda"] = Field(
        default="cuda", description="Local embedding inference device; CUDA is required by default"
    )
    embedding_normalize: bool = Field(
        default=True, description="L2-normalize embeddings before Neo4j cosine search"
    )
    embedding_cache_dir: str = Field(
        default=".models", description="Local cache directory for downloaded embedding models"
    )

    # Cache & Task Queue (Redis & Celery)
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1", description="Celery message broker URL"
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2", description="Celery result backend URL"
    )
    ingestion_worker_count: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Number of in-process ingestion workers for the local web runtime",
    )

    # MCP Server Settings
    mcp_server_name: str = Field(default="knowledge-baas-mcp", description="MCP Server identifier")
    mcp_server_host: str = Field(default="0.0.0.0", description="MCP Server host")
    mcp_server_port: int = Field(default=8001, description="MCP Server port")

    # Evaluation & Backlog Settings
    ragas_enabled: bool = Field(default=True, description="Enable RAGAs metric calculation")
    backlog_dir: str = Field(default="backlog", description="Directory storing hallucination and error backlogs")

    # MinerU API ingestion backend
    mineru_base_url: str = Field(default="http://localhost:8000", description="MinerU API base URL")
    mineru_api_key: Optional[str] = Field(default=None, description="Optional MinerU API bearer token")
    mineru_backend: str = Field(default="pipeline", description="MinerU backend (pipeline or vlm)")
    mineru_parse_method: str = Field(default="auto", description="MinerU parse method")
    mineru_timeout_seconds: float = Field(default=600.0, ge=1.0, description="MinerU request timeout")
    mineru_max_retries: int = Field(default=2, ge=0, le=5, description="MinerU transient retry count")
    mineru_retry_backoff_seconds: float = Field(default=1.0, ge=0.0, description="MinerU retry backoff")
    mineru_artifact_dir: str = Field(
        default=".artifacts/mineru",
        description="Local directory for downloaded MinerU zip artifacts",
    )

    @model_validator(mode="after")
    def validate_cross_field_limits(self) -> "Settings":
        """Reject configurations that would make chunking or vector search invalid."""
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_MAX_TOKENS")
        if self.embedding_enabled and self.embedding_provider == "none":
            raise ValueError("EMBEDDING_PROVIDER must be configured when EMBEDDING_ENABLED=true")
        if self.app_env == "production":
            if len(self.session_secret) < 32 or self.session_secret == "a-rag-development-session-key-change-before-production":
                raise ValueError("SESSION_SECRET must be a long random value in production")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE must be true in production")
        return self


def setup_logger(settings: Settings) -> None:
    """Configure structured logging with a UTF-8-safe process output stream.

    Windows can expose ``sys.stdout`` as ``cp1252``.  That encoding cannot
    serialize valid Unicode document content or structured log messages, so
    Loguru's queued writer can emit a secondary ``UnicodeEncodeError`` while
    the actual request is still running.  UTF-8 is the process log contract;
    ``backslashreplace`` preserves the event instead of allowing logging to
    interrupt application diagnostics.
    """
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfigure):
        try:
            stdout_reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # Embedded hosts and test capture streams may not permit stream
            # reconfiguration.  They generally provide their own Unicode-safe
            # writer, so logger setup should remain non-fatal in that case.
            pass

    logger.remove()

    if settings.log_format_json:
        # JSON formatting for MCP protocol and structured observability
        logger.add(
            sys.stdout,
            level=settings.log_level.upper(),
            serialize=True,
            enqueue=True,
            backtrace=settings.debug,
            diagnose=settings.debug,
        )
    else:
        logger.add(
            sys.stdout,
            level=settings.log_level.upper(),
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            enqueue=True,
            colorize=True,
            backtrace=settings.debug,
            diagnose=settings.debug,
        )


settings = Settings()
setup_logger(settings)

__all__ = ["Settings", "settings", "logger", "setup_logger"]
