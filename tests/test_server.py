"""Tests for :mod:`qilin.server` HTTP endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from qilin import server as server_module


@pytest.fixture
def client(mocker) -> TestClient:
    """Build a fresh app with a healthy embedder + store and yield a TestClient.

    We patch `get_store` / `get_embedder` in the server module so handlers see
    AsyncMock dependencies rather than real Qdrant/Ollama clients.
    """
    fake_store = AsyncMock()
    fake_store.health.return_value = True

    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True

    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())

    app = server_module._build_app()
    with TestClient(app) as c:
        yield c


def test_root_returns_metadata(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "qilin"
    assert body["transport"] == "sse"
    assert body["endpoints"]["sse"] == "/sse"
    assert body["endpoints"]["health"] == "/healthz"
    assert "version" in body
    assert "embedding_model" in body


def test_healthz_returns_200_when_all_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["qdrant"] == "ok"
    assert body["embedder"] == "ok"


def test_healthz_returns_503_when_qdrant_down(mocker) -> None:
    fake_store = AsyncMock()
    fake_store.health.return_value = False
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True

    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())

    app = server_module._build_app()
    with TestClient(app) as c:
        response = c.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["qdrant"] == "down"
    assert body["embedder"] == "ok"


def test_healthz_returns_503_when_embedder_down(mocker) -> None:
    fake_store = AsyncMock()
    fake_store.health.return_value = True
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = False

    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())

    app = server_module._build_app()
    with TestClient(app) as c:
        response = c.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["embedder"] == "down"


def test_build_mcp_registers_tools() -> None:
    mcp = server_module._build_mcp()

    assert mcp.name == "qilin"
    assert "Qilin" in (mcp.instructions or "")


def test_root_advertises_mcp_endpoint_when_streamable_http_enabled(
    mocker,
) -> None:
    fake_store = AsyncMock()
    fake_store.health.return_value = True
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True
    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())
    mocker.patch.object(server_module, "shutdown_sparse", AsyncMock())
    mocker.patch.object(server_module, "shutdown_reranker", AsyncMock())

    settings = server_module.get_settings()
    mocker.patch.object(settings, "streamable_http_enabled", True)

    app = server_module._build_app()
    with TestClient(app) as c:
        body = c.get("/").json()
        assert body["endpoints"].get("mcp") == "/mcp"


def test_root_omits_mcp_endpoint_when_streamable_http_disabled(mocker) -> None:
    fake_store = AsyncMock()
    fake_store.health.return_value = True
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True
    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())
    mocker.patch.object(server_module, "shutdown_sparse", AsyncMock())
    mocker.patch.object(server_module, "shutdown_reranker", AsyncMock())

    settings = server_module.get_settings()
    mocker.patch.object(settings, "streamable_http_enabled", False)

    app = server_module._build_app()
    with TestClient(app) as c:
        body = c.get("/").json()
        assert "mcp" not in body["endpoints"]


def test_ttl_sweep_loop_skips_when_no_ttl_collections(mocker) -> None:
    """The sweeper should only spawn when at least one collection has TTL."""
    fake_store = AsyncMock()
    fake_store.health.return_value = True
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True
    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())
    mocker.patch.object(server_module, "shutdown_sparse", AsyncMock())
    mocker.patch.object(server_module, "shutdown_reranker", AsyncMock())

    sweep_mock = mocker.patch.object(server_module, "_ttl_sweep_loop")

    app = server_module._build_app()
    with TestClient(app):
        pass

    sweep_mock.assert_not_called()
