"""Tests for :mod:`qilin.auth` bearer-token middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from qilin import server as server_module
from qilin.auth import BearerAuthMiddleware


def _make_app(tokens: str | list[str] | None) -> Starlette:
    async def hello(request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def health(request) -> JSONResponse:
        return JSONResponse({"ok": True})

    routes = [
        Route("/", health, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/protected", hello, methods=["GET"]),
    ]
    middleware = [Middleware(BearerAuthMiddleware, tokens=tokens)]
    return Starlette(routes=routes, middleware=middleware)


def test_middleware_disabled_when_no_tokens() -> None:
    app = _make_app(None)
    with TestClient(app) as c:
        r = c.get("/protected")
        assert r.status_code == 200


def test_middleware_rejects_missing_header() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        r = c.get("/protected")
        assert r.status_code == 401
        body = r.json()
        assert body["error"] == "unauthorized"
        assert "missing" in body["detail"]


def test_middleware_rejects_wrong_token() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        r = c.get("/protected", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        assert r.json()["error"] == "unauthorized"


def test_middleware_rejects_non_bearer_scheme() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        r = c.get("/protected", headers={"Authorization": "Basic c2VjcmV0"})
        assert r.status_code == 401


def test_middleware_accepts_correct_token() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        r = c.get("/protected", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}


def test_middleware_supports_token_rotation() -> None:
    app = _make_app(["old-token", "new-token"])
    with TestClient(app) as c:
        for tok in ("old-token", "new-token"):
            r = c.get("/protected", headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 200, tok


def test_middleware_excludes_health_and_root() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        for path in ("/", "/healthz"):
            r = c.get(path)
            assert r.status_code == 200, path


def test_middleware_response_advertises_bearer() -> None:
    app = _make_app("s3cret")
    with TestClient(app) as c:
        r = c.get("/protected")
        assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_server_attaches_middleware_when_auth_token_set(mocker) -> None:
    fake_store = AsyncMock()
    fake_store.health.return_value = True
    fake_embedder = AsyncMock()
    fake_embedder.health.return_value = True

    mocker.patch.object(
        server_module, "get_store", AsyncMock(return_value=fake_store)
    )
    mocker.patch.object(
        server_module, "get_embedder", AsyncMock(return_value=fake_embedder)
    )
    mocker.patch.object(server_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(server_module, "shutdown_store", AsyncMock())
    mocker.patch.object(server_module, "shutdown_sparse", AsyncMock())
    mocker.patch.object(server_module, "shutdown_reranker", AsyncMock())

    settings = server_module.get_settings()
    mocker.patch.object(settings, "auth_token", "s3cret")

    app = server_module._build_app()
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/").status_code == 200
        body = c.get("/").json()
        assert body["auth"] == "bearer"
