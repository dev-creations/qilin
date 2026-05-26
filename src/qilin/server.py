"""Qilin MCP server: Starlette app that mounts FastMCP's SSE transport.

The resulting ASGI ``app`` is launched by ``scripts/entrypoint.sh`` via
``uvicorn`` with TLS termination, so the canonical endpoint is::

    https://<host>:8443/sse
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__, tools
from .auth import BearerAuthMiddleware
from .config import get_settings
from .embeddings import get_embedder, shutdown_embedder
from .reranker import shutdown_reranker
from .sparse import shutdown_sparse
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
        language: str | None = None,
    ) -> dict[str, Any]:
        """Store text in vector memory. Long inputs are chunked automatically.

        Args:
            text: Raw text to remember. Can be arbitrarily long.
            collection: Memory namespace. Defaults to the server's default collection.
            metadata: Optional key/value metadata attached to every chunk.
            source: Logical identifier (e.g. file path or URL); ingesting the
                same text under the same source is idempotent.
            language: Optional language hint (``python``, ``go``,
                ``typescript``, ``rust``, ...). When provided and supported,
                Qilin chunks on AST boundaries and indexes ``defines`` per
                chunk so ``recall(filter={"defines": "MyClass"})`` works.
        """
        return await tools.remember(
            text=text,
            collection=collection,
            metadata=metadata,
            source=source,
            language=language,
        )

    @mcp.tool()
    async def recall(
        query: str,
        collection: str | None = None,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        score_threshold: float | None = None,
        context_window: int = 0,
        group_by_source: bool = False,
        mmr_lambda: float | None = None,
        mode: str | None = None,
        rerank: bool | None = None,
        rerank_top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search vector memory for chunks most relevant to a natural-language query.

        Returns a flat list of hits ordered by descending score. Each hit is a
        dict with first-class fields promoted to the top level:
        ``id``, ``score``, ``text``, ``source``, ``language``, ``start_line``,
        ``end_line``, ``lines`` (e.g. ``"30-95"``), ``git_sha``,
        ``chunk_ordinal``, ``chunk_count``, ``document_hash``, ``created_at``.
        Anything caller-supplied via ``remember(metadata=...)`` lives under
        ``extra_metadata``.

        Args:
            query: Natural-language query.
            collection: Memory namespace to search.
            top_k: Maximum number of hits to return.
            filter: Optional payload filter (e.g. ``{"source": "notes.md"}``).
            score_threshold: Drop hits below this cosine similarity score.
            context_window: When > 0, fetch +/- N sibling chunks per hit (same
                ``source``) and merge them into one contiguous text block.
            group_by_source: When True, keep at most one hit per ``source``.
            mmr_lambda: When set in ``(0, 1]``, re-rank candidates by Maximal
                Marginal Relevance for diversity. ``1.0`` is plain similarity.
            mode: ``"dense"`` | ``"sparse"`` | ``"hybrid"``. Defaults to
                hybrid when the server has ``hybrid_enabled`` on.
            rerank: When True (or unset and ``rerank_enabled`` is on), apply
                a cross-encoder reranker over the candidate pool.
            rerank_top_k: Candidate pool size fed to the reranker.
        """
        return await tools.recall(
            query=query,
            collection=collection,
            top_k=top_k,
            filter=filter,
            score_threshold=score_threshold,
            context_window=context_window,
            group_by_source=group_by_source,
            mmr_lambda=mmr_lambda,
            mode=mode,
            rerank=rerank,
            rerank_top_k=rerank_top_k,
        )

    @mcp.tool()
    async def recall_files(
        query: str,
        collection: str | None = None,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        score_threshold: float | None = None,
        mode: str | None = None,
        rerank: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top-K source *files* relevant to a query.

        Pulls a larger pool of chunk hits, groups them by ``source``, and
        returns one entry per file ordered by summed score. Each entry has
        ``source``, ``score`` (sum), ``top_score`` (best chunk score),
        ``hit_count``, ``preview``, ``lines`` (best chunk span), and
        ``language``. Far cheaper for an LLM than asking it to dedupe a
        chunk-level recall response itself.
        """
        return await tools.recall_files(
            query=query,
            collection=collection,
            top_k=top_k,
            filter=filter,
            score_threshold=score_threshold,
            mode=mode,
            rerank=rerank,
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

    @mcp.tool()
    async def mark_useful(
        id: str,
        useful: bool = True,
        collection: str | None = None,
    ) -> dict[str, Any]:
        """Mark a recall hit useful (or not). Boosts future recall scores.

        Pass the ``id`` field of a recall hit. ``useful=True`` adds +1 to the
        chunk's stored feedback counter; ``useful=False`` subtracts 1. The
        next ``recall`` applies a small score multiplier proportional to
        net feedback (capped at +/- 50 percent).
        """
        return await tools.mark_useful(id=id, useful=useful, collection=collection)

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
    endpoints: dict[str, str] = {
        "sse": "/sse",
        "messages": "/messages/",
        "health": "/healthz",
    }
    if settings.streamable_http_enabled:
        endpoints["mcp"] = "/mcp"
    return JSONResponse(
        {
            "name": "qilin",
            "version": __version__,
            "transport": "sse",
            "endpoints": endpoints,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "default_collection": settings.default_collection,
            "auth": "bearer" if settings.auth_token else "open",
        }
    )


async def _ttl_sweep_loop(stop_event: asyncio.Event) -> None:
    """Periodically delete expired chunks from collections with ``ttl_seconds`` set.

    Runs every ``settings.ttl_sweep_seconds`` until ``stop_event`` fires.
    """
    settings = get_settings()
    if settings.ttl_sweep_seconds <= 0:
        return
    while not stop_event.is_set():
        try:
            store = await get_store()
            tracked = [
                name
                for name, override in settings.collections.items()
                if override.ttl_seconds is not None
            ]
            if tracked:
                now_iso = datetime.now(UTC).isoformat()
                for name in tracked:
                    try:
                        swept = await store.sweep_expired(name, now_iso=now_iso)
                        if swept:
                            logger.info("TTL sweep: %s removed=%s", name, swept)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("TTL sweep error on %s: %s", name, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTL sweep loop error: %s", exc)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.ttl_sweep_seconds
            )
        except TimeoutError:
            continue


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_app() -> Starlette:
    _configure_logging()
    settings = get_settings()
    mcp = _build_mcp()

    stop_event = asyncio.Event()
    sweep_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(app: Starlette):
        nonlocal sweep_task
        logger.info("Qilin %s starting (SSE transport)", __version__)
        if any(o.ttl_seconds is not None for o in settings.collections.values()):
            sweep_task = asyncio.create_task(_ttl_sweep_loop(stop_event))
        try:
            yield
        finally:
            logger.info("Qilin shutting down; closing clients")
            stop_event.set()
            if sweep_task is not None:
                try:
                    await asyncio.wait_for(sweep_task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    sweep_task.cancel()
            await shutdown_embedder()
            await shutdown_store()
            await shutdown_sparse()
            await shutdown_reranker()

    sse_app = mcp.sse_app()
    routes: list = [
        Route("/", _root, methods=["GET"]),
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    if settings.streamable_http_enabled:
        try:
            streamable_app = mcp.streamable_http_app()
            routes.append(Mount("/mcp", app=streamable_app))
        except Exception as exc:  # noqa: BLE001
            logger.warning("streamable HTTP unavailable: %s", exc)
    routes.append(Mount("/", app=sse_app))

    middleware = []
    if settings.auth_token:
        middleware.append(
            Middleware(BearerAuthMiddleware, tokens=settings.auth_token)
        )
        logger.info("Bearer-token auth enabled")

    app = Starlette(
        debug=False,
        lifespan=lifespan,
        routes=routes,
        middleware=middleware,
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
