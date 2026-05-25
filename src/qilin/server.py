"""Qilin MCP server: Starlette app that mounts FastMCP's SSE transport.

The resulting ASGI ``app`` is launched by ``scripts/entrypoint.sh`` via
``uvicorn`` with TLS termination, so the canonical endpoint is::

    https://<host>:8443/sse
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__, tools
from .config import get_settings
from .embeddings import get_embedder, shutdown_embedder
from .store import get_store, shutdown_store

logger = logging.getLogger(__name__)


def _build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="qilin",
        instructions=(
            "Qilin is a Qdrant-backed vector memory. Use `remember` to store text "
            "(it will be chunked automatically), `recall` to retrieve the most "
            "relevant chunks for a query, and `forget` to delete entries. "
            "`list_collections`, `create_collection`, and `stats` manage memory namespaces."
        ),
    )

    @mcp.tool()
    async def remember(
        text: str,
        collection: str | None = None,
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Store text in vector memory. Long inputs are chunked automatically.

        Args:
            text: Raw text to remember. Can be arbitrarily long.
            collection: Memory namespace. Defaults to the server's default collection.
            metadata: Optional key/value metadata attached to every chunk.
            source: Logical identifier (e.g. file path or URL); ingesting the
                same text under the same source is idempotent.
        """
        return await tools.remember(
            text=text,
            collection=collection,
            metadata=metadata,
            source=source,
        )

    @mcp.tool()
    async def recall(
        query: str,
        collection: str | None = None,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search vector memory for chunks most relevant to a natural-language query.

        Args:
            query: Natural-language query.
            collection: Memory namespace to search.
            top_k: Maximum number of hits to return.
            filter: Optional payload filter (e.g. ``{"source": "notes.md"}``).
            score_threshold: Drop hits below this cosine similarity score.
        """
        return await tools.recall(
            query=query,
            collection=collection,
            top_k=top_k,
            filter=filter,
            score_threshold=score_threshold,
        )

    @mcp.tool()
    async def forget(
        ids: list[str] | None = None,
        collection: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delete entries by id list or by payload filter.

        Either ``ids`` or ``filter`` must be provided.
        """
        return await tools.forget(ids=ids, collection=collection, filter=filter)

    @mcp.tool()
    async def list_collections() -> list[str]:
        """List all memory collections."""
        return await tools.list_collections()

    @mcp.tool()
    async def create_collection(name: str) -> dict[str, Any]:
        """Create a new memory collection. Idempotent."""
        return await tools.create_collection(name=name)

    @mcp.tool()
    async def stats(collection: str | None = None) -> dict[str, Any]:
        """Return basic stats (point count, status) for a collection."""
        return await tools.stats(collection=collection)

    return mcp


async def _healthz(request) -> JSONResponse:
    store = await get_store()
    embedder = await get_embedder()
    db_ok = await store.health()
    embed_ok = await embedder.health()
    ok = db_ok and embed_ok
    body = {
        "ok": ok,
        "version": __version__,
        "qdrant": "ok" if db_ok else "down",
        "embedder": "ok" if embed_ok else "down",
    }
    return JSONResponse(body, status_code=200 if ok else 503)


async def _root(request) -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "name": "qilin",
            "version": __version__,
            "transport": "sse",
            "endpoints": {
                "sse": "/sse",
                "messages": "/messages/",
                "health": "/healthz",
            },
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "default_collection": settings.default_collection,
        }
    )


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_app() -> Starlette:
    _configure_logging()
    mcp = _build_mcp()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        logger.info("Qilin %s starting (SSE transport)", __version__)
        try:
            yield
        finally:
            logger.info("Qilin shutting down; closing clients")
            await shutdown_embedder()
            await shutdown_store()

    sse_app = mcp.sse_app()

    app = Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/", _root, methods=["GET"]),
            Route("/healthz", _healthz, methods=["GET"]),
            Mount("/", app=sse_app),
        ],
    )
    return app


app = _build_app()


def main() -> None:
    """Console entrypoint; rarely used because the container uses uvicorn directly."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "qilin.server:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        ssl_certfile=settings.tls_cert_file if os.path.exists(settings.tls_cert_file) else None,
        ssl_keyfile=settings.tls_key_file if os.path.exists(settings.tls_key_file) else None,
    )


if __name__ == "__main__":
    main()
