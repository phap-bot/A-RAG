"""Core system exceptions for Enterprise Agentic RAG."""

from typing import Any, Dict, Optional


class AppException(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(AppException):
    """Raised when application configuration or environment variables are invalid."""
    pass


class IngestionError(AppException):
    """Raised during document ingestion pipeline failures."""
    pass


class ParsingError(IngestionError):
    """Raised when document parser or layout analysis fails."""
    pass


class RetrievalError(AppException):
    """Base exception for retrieval failures."""
    pass


class VectorDBError(RetrievalError):
    """Raised when Vector DB queries or connections fail."""
    pass


class GraphDBError(RetrievalError):
    """Raised when Neo4j or Cypher queries fail."""
    pass


class SemanticCacheError(AppException):
    """Raised when Redis semantic cache operations fail."""
    pass


class AgentExecutionError(AppException):
    """Raised during LangGraph agent execution."""
    pass


class CriticReflectionError(AgentExecutionError):
    """Raised when critic evaluation or reflection loop encounters an unrecoverable state."""
    pass


class MaxRetriesExceededError(CriticReflectionError):
    """Raised when reflection loop exceeds maximum retries without passing quality criteria."""
    pass


class MCPGatewayError(AppException):
    """Raised when Model Context Protocol (MCP) server or tool execution fails."""
    pass
