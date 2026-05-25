"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ollama_base_url: str = Field(
        default="http://host.docker.internal:11434",
        description="Base URL of the Ollama server providing the embedding model.",
    )
    embedding_model: str = Field(
        default="nomic-embed-text-v2-moe",
        description="Ollama model name used to embed text.",
    )
    embedding_dim: int = Field(
        default=768,
        description="Dimension of the embedding vectors produced by the model.",
    )

    qdrant_url: str = Field(
        default="http://qdrant:6333",
        description="HTTP URL of the Qdrant instance.",
    )
    qdrant_api_key: str | None = Field(
        default=None,
        description="Optional API key for Qdrant (leave unset for the local compose setup).",
    )

    default_collection: str = Field(
        default="memory",
        description="Collection used when callers omit one.",
    )

    chunk_size_tokens: int = Field(
        default=450,
        ge=32,
        description="Target token count per chunk; must stay below the embedder's context window.",
    )
    chunk_overlap_tokens: int = Field(
        default=50,
        ge=0,
        description="Token overlap between consecutive chunks to preserve context across boundaries.",
    )
    embed_batch_size: int = Field(
        default=16,
        ge=1,
        description="Max number of inputs sent to Ollama in a single /api/embed call.",
    )

    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8443)
    tls_cert_file: str = Field(default="/certs/cert.pem")
    tls_key_file: str = Field(default="/certs/key.pem")

    http_timeout_seconds: float = Field(default=120.0, ge=1.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
