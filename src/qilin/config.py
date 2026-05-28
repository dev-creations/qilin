"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CollectionOverride(BaseModel):
    """Per-collection knobs that override the global defaults.

    Currently scoped to chunker tuning and TTL - per-collection embedding
    model routing is future work because it requires per-collection vector
    dim (which today is collection-schema-level).
    """

    chunk_size_tokens: int | None = Field(
        default=None, ge=32, description="Override chunk_size_tokens for this collection."
    )
    chunk_overlap_tokens: int | None = Field(
        default=None, ge=0, description="Override chunk_overlap_tokens for this collection."
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "When set, every chunk written to this collection gets an "
            "``expires_at`` payload field and a background sweeper deletes "
            "expired chunks. Useful for scratch / session memory."
        ),
    )


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

    hybrid_enabled: bool = Field(
        default=False,
        description=(
            "When True, new collections are created with dense + BM25 sparse "
            "named vectors and `recall` defaults to hybrid retrieval. Opt-in "
            "so existing single-vector collections keep working unchanged."
        ),
    )
    sparse_model: str = Field(
        default="Qdrant/bm25",
        description="FastEmbed sparse model used to compute BM25 vectors.",
    )
    rerank_enabled: bool = Field(
        default=False,
        description=(
            "When True, `recall` runs a cross-encoder reranker over the top "
            "candidates. Loads a ~80 MB FastEmbed model on first use."
        ),
    )
    rerank_model: str = Field(
        default="Xenova/ms-marco-MiniLM-L-6-v2",
        description="FastEmbed TextCrossEncoder model used for reranking.",
    )
    rerank_top_k: int = Field(
        default=50,
        ge=1,
        description="Candidate pool size fed into the reranker.",
    )

    collections: dict[str, CollectionOverride] = Field(
        default_factory=dict,
        description=(
            "Per-collection overrides keyed by collection name. Supports "
            "chunk_size_tokens, chunk_overlap_tokens, and ttl_seconds."
        ),
    )

    auth_token: str | list[str] | None = Field(
        default=None,
        description=(
            "Bearer token(s) required on incoming MCP requests. May be a "
            "string or a list (for rotation). When unset, no auth is "
            "enforced (single-tenant localhost default)."
        ),
    )
    streamable_http_enabled: bool = Field(
        default=True,
        description=(
            "Mount FastMCP's streamable HTTP transport at ``/mcp`` alongside "
            "the existing ``/sse`` endpoint. Newer MCP clients prefer this."
        ),
    )
    ttl_sweep_seconds: int = Field(
        default=300,
        ge=10,
        description=(
            "How often the in-process TTL sweeper deletes expired chunks "
            "from collections with ttl_seconds set."
        ),
    )
    workspace_scoping_enabled: bool = Field(
        default=True,
        description=(
            "When true, recall tools auto-scope results to the active workspace "
            "when workspace folder metadata is available from the MCP client."
        ),
    )
    workspace_scoping_mode: str = Field(
        default="prefix_filter",
        description=(
            "Workspace scoping strategy: `prefix_filter`, `per_project_collection`, or "
            "`hybrid`."
        ),
    )
    workspace_use_project_collection: bool = Field(
        default=False,
        description=(
            "Enable dynamic project-specific collection routing. In `hybrid` mode this "
            "can be combined with source-prefix filtering."
        ),
    )
    workspace_path_mappings: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional host/container path prefix mappings used to normalize workspace "
            "and source paths before scoping."
        ),
    )

    recall_log_path: str | None = Field(
        default=None,
        description=(
            "Path to the JSONL recall-log file. When unset, defaults to "
            "``~/.qilin/logs/recall.jsonl``. Set to an empty string to "
            "disable logging."
        ),
    )

    def for_collection(self, name: str) -> Settings:
        """Return a settings view with per-collection overrides applied.

        The returned instance is a *copy* of self with override fields
        replaced; callers that key off ``chunk_size_tokens`` /
        ``chunk_overlap_tokens`` automatically see the per-collection values.
        """
        override = self.collections.get(name)
        if override is None:
            return self
        patch: dict[str, object] = {}
        if override.chunk_size_tokens is not None:
            patch["chunk_size_tokens"] = override.chunk_size_tokens
        if override.chunk_overlap_tokens is not None:
            patch["chunk_overlap_tokens"] = override.chunk_overlap_tokens
        if not patch:
            return self
        return self.model_copy(update=patch)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
