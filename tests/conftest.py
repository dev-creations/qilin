"""Shared pytest fixtures for the Qilin test suite."""

from __future__ import annotations

import os
from typing import Any

import pytest

from qilin import config as config_module

_QILIN_ENV_PREFIXES: tuple[str, ...] = (
    "OLLAMA_",
    "QDRANT_",
    "EMBEDDING_",
    "CHUNK_",
    "MCP_",
    "TLS_",
)

_QILIN_ENV_EXACT: frozenset[str] = frozenset(
    {
        "DEFAULT_COLLECTION",
        "EMBED_BATCH_SIZE",
        "HTTP_TIMEOUT_SECONDS",
    }
)


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch: pytest.MonkeyPatch):
    """Reset env vars and the cached `Settings` so each test starts from defaults.

    Also disables `.env` file loading so a developer's local `.env` cannot
    leak into the test run.
    """
    for key in list(os.environ.keys()):
        if key in _QILIN_ENV_EXACT or any(key.startswith(p) for p in _QILIN_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)

    original_config = dict(config_module.Settings.model_config)
    disabled_config = {**original_config, "env_file": None}
    monkeypatch.setattr(config_module.Settings, "model_config", disabled_config)

    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


@pytest.fixture
def settings_factory() -> Any:
    """Return a factory that builds `Settings` instances with overrides."""

    def _make(**overrides: Any) -> config_module.Settings:
        return config_module.Settings(**overrides)

    return _make
