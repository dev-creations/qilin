"""Tests for :mod:`qilin.server` HTTP endpoints."""

from __future__ import annotations

from types import SimpleNamespace
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


def test_streamable_http_failure_falls_back_to_sse_only(mocker, caplog) -> None:
    """If FastMCP.streamable_http_app() blows up, the server still boots."""
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

    fake_mcp = mocker.MagicMock()
    fake_mcp.sse_app.return_value = mocker.MagicMock()
    fake_mcp.streamable_http_app.side_effect = RuntimeError("transport missing")
    mocker.patch.object(server_module, "_build_mcp", return_value=fake_mcp)

    with caplog.at_level("WARNING"):
        app = server_module._build_app()
        with TestClient(app) as c:
            assert c.get("/healthz").status_code == 200

    assert any(
        "streamable HTTP unavailable" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_ttl_sweep_loop_sweeps_then_exits_on_stop(mocker) -> None:
    """One sweep, then the stop event lets the loop return cleanly."""
    import asyncio

    settings = server_module.get_settings()
    from qilin.config import CollectionOverride

    mocker.patch.object(
        settings,
        "collections",
        {"scratch": CollectionOverride(ttl_seconds=60)},
    )
    mocker.patch.object(settings, "ttl_sweep_seconds", 1)

    fake_store = AsyncMock()
    fake_store.sweep_expired.return_value = 3
    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))

    stop_event = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(
        server_module._ttl_sweep_loop(stop_event),
        stop_soon(),
    )

    fake_store.sweep_expired.assert_awaited()
    args, kwargs = fake_store.sweep_expired.await_args
    assert args[0] == "scratch"
    assert "now_iso" in kwargs


@pytest.mark.asyncio
async def test_ttl_sweep_loop_swallows_per_collection_errors(mocker) -> None:
    """One bad collection should not stop the loop or take down the server."""
    import asyncio

    settings = server_module.get_settings()
    from qilin.config import CollectionOverride

    mocker.patch.object(
        settings,
        "collections",
        {
            "broken": CollectionOverride(ttl_seconds=60),
            "good": CollectionOverride(ttl_seconds=60),
        },
    )
    mocker.patch.object(settings, "ttl_sweep_seconds", 1)

    fake_store = AsyncMock()

    async def maybe_fail(name, *, now_iso):
        if name == "broken":
            raise RuntimeError("qdrant down")
        return 1

    fake_store.sweep_expired.side_effect = maybe_fail
    mocker.patch.object(server_module, "get_store", AsyncMock(return_value=fake_store))

    stop_event = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(
        server_module._ttl_sweep_loop(stop_event),
        stop_soon(),
    )

    assert fake_store.sweep_expired.await_count >= 2


@pytest.mark.asyncio
async def test_ttl_sweep_loop_returns_immediately_when_sweep_seconds_nonpositive(
    mocker,
) -> None:
    import asyncio

    settings = server_module.get_settings()
    mocker.patch.object(settings, "ttl_sweep_seconds", 0)

    stop_event = asyncio.Event()
    await server_module._ttl_sweep_loop(stop_event)


class TestRegisteredTools:
    """Drive the FastMCP tool wrappers in :func:`_build_mcp` directly.

    Each registered tool just forwards into ``qilin.tools.*``; these tests
    cover the forward path by mocking the underlying tool helpers.
    """

    @pytest.fixture
    def patched_tools(self, mocker):
        return SimpleNamespace(
            remember=mocker.patch.object(
                server_module.tools, "remember", AsyncMock(return_value={"ok": 1})
            ),
            recall=mocker.patch.object(
                server_module.tools,
                "recall",
                AsyncMock(return_value=[{"id": "a"}]),
            ),
            recall_files=mocker.patch.object(
                server_module.tools,
                "recall_files",
                AsyncMock(return_value=[{"source": "f.py"}]),
            ),
            forget=mocker.patch.object(
                server_module.tools, "forget", AsyncMock(return_value={"deleted": 2})
            ),
            list_collections=mocker.patch.object(
                server_module.tools,
                "list_collections",
                AsyncMock(return_value=["memory"]),
            ),
            create_collection=mocker.patch.object(
                server_module.tools,
                "create_collection",
                AsyncMock(return_value={"name": "x", "created": True}),
            ),
            stats=mocker.patch.object(
                server_module.tools,
                "stats",
                AsyncMock(return_value={"exists": True}),
            ),
            mark_useful=mocker.patch.object(
                server_module.tools,
                "mark_useful",
                AsyncMock(return_value={"id": "a", "feedback": 1}),
            ),
        )

    @pytest.mark.asyncio
    async def test_remember_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        result = await mcp.call_tool("remember", {"text": "hi"})
        assert result is not None
        patched_tools.remember.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recall_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("recall", {"query": "q"})
        patched_tools.recall.assert_awaited_once()
        kwargs = patched_tools.recall.await_args.kwargs
        assert kwargs["query"] == "q"

    @pytest.mark.asyncio
    async def test_recall_wrapper_extracts_workspace_roots(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool(
            "recall",
            {
                "query": "q",
                "ctx": {
                    "initialize_params": {
                        "workspaceFolders": [{"uri": "file:///repo", "name": "repo"}]
                    }
                },
            },
        )
        kwargs = patched_tools.recall.await_args.kwargs
        assert kwargs["workspace_roots"] == ["/repo"]

    @pytest.mark.asyncio
    async def test_recall_files_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("recall_files", {"query": "q"})
        patched_tools.recall_files.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forget_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("forget", {"ids": ["a", "b"]})
        patched_tools.forget.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_collections_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("list_collections", {})
        patched_tools.list_collections.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_collection_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("create_collection", {"name": "scratch"})
        patched_tools.create_collection.assert_awaited_once_with(name="scratch")

    @pytest.mark.asyncio
    async def test_stats_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("stats", {})
        patched_tools.stats.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_useful_wrapper_forwards(self, patched_tools) -> None:
        mcp = server_module._build_mcp()
        await mcp.call_tool("mark_useful", {"id": "abc", "useful": True})
        patched_tools.mark_useful.assert_awaited_once()
