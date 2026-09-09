"""System configuration and structured JSON logging setup using Pydantic Settings and Loguru."""

import os
import sys
from typing import Literal, Optional
from loguru import logger
from pydantic import Field
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

    # API Configuration
    api_host: str = Field(default="0.0.0.0", description="FastAPI host")
    api_port: int = Field(default=8000, description="FastAPI port")
    api_prefix: str = Field(default="/api/v1", description="FastAPI route prefix")

    # LLM & Embedding Settings
    openai_api_key: str = Field(default="", description="OpenAI API Key")
    openai_base_url: Optional[str] = Field(default="https://api.openai.com/v1", description="OpenAI API Base URL")
    primary_llm_model: str = Field(default="gpt-4o", description="Primary Chat LLM model")
    vlm_model_name: str = Field(default="gpt-4o", description="Vision-Language Model for multimodal ingestion")
    embedding_model_name: str = Field(
        default="text-embedding-3-small", description="Embedding model name"
    )
    embedding_dimension: int = Field(default=1536, description="Embedding vector dimension")

    # Agentic Reflection Loop
    max_reflection_retries: int = Field(
        default=3, ge=1, le=5, description="Maximum self-reflection retries for critic agent"
    )

    # Vector Database Settings
    vector_db_provider: Literal["qdrant", "milvus", "elasticsearch", "mock"] = Field(
        default="qdrant", description="Vector database provider"
    )
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant service URL")
    qdrant_api_key: Optional[str] = Field(default=None, description="Qdrant API key")
    qdrant_collection: str = Field(default="enterprise_kb", description="Default Qdrant collection name")

    # Knowledge Graph (Neo4j) Settings
    neo4j_uri: str = Field(default="bolt://localhost:7687", description="Neo4j bolt URI")
    neo4j_user: str = Field(default="neo4j", description="Neo4j username")
    neo4j_password: str = Field(default="password123", description="Neo4j password")

    # Cache & Task Queue (Redis & Celery)
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1", description="Celery message broker URL"
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2", description="Celery result backend URL"
    )

    # MCP Server Settings
    mcp_server_name: str = Field(default="knowledge-baas-mcp", description="MCP Server identifier")
    mcp_server_host: str = Field(default="0.0.0.0", description="MCP Server host")
    mcp_server_port: int = Field(default=8001, description="MCP Server port")

    # Evaluation & Backlog Settings
    ragas_enabled: bool = Field(default=True, description="Enable RAGAs metric calculation")
    backlog_dir: str = Field(default="backlog", description="Directory storing hallucination and error backlogs")


def setup_logger(settings: Settings) -> None:
    """Configure Loguru logger with JSON formatting for MCP and system-wide observability."""
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
