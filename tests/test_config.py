"""Tests for :mod:`qilin.config`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qilin.config import Settings, get_settings


def test_defaults_match_documented_values() -> None:
    s = Settings()

    assert s.ollama_base_url == "http://host.docker.internal:11434"
    assert s.embedding_model == "nomic-embed-text-v2-moe"
    assert s.embedding_dim == 768
    assert s.qdrant_url == "http://qdrant:6333"
    assert s.qdrant_api_key is None
    assert s.default_collection == "memory"
    assert s.chunk_size_tokens == 450
    assert s.chunk_overlap_tokens == 50
    assert s.parent_child_enabled is False
    assert s.parent_chunk_size_tokens == 900
    assert s.child_chunk_size_tokens == 180
    assert s.embed_batch_size == 16
    assert s.mcp_host == "0.0.0.0"
    assert s.mcp_port == 8443
    assert s.workspace_scoping_enabled is True
    assert s.workspace_scoping_mode == "prefix_filter"
    assert s.workspace_use_project_collection is False
    assert s.workspace_path_mappings == {}


def test_env_overrides_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.test:9999")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    monkeypatch.setenv("DEFAULT_COLLECTION", "custom")
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "256")

    s = Settings()

    assert s.ollama_base_url == "http://example.test:9999"
    assert s.embedding_dim == 1024
    assert s.default_collection == "custom"
    assert s.chunk_size_tokens == 256


def test_chunk_size_tokens_must_be_at_least_32() -> None:
    with pytest.raises(ValidationError):
        Settings(chunk_size_tokens=16)


def test_chunk_overlap_tokens_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(chunk_overlap_tokens=-1)


def test_embed_batch_size_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        Settings(embed_batch_size=0)


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()

    assert a is b
