"""Pure-Python implementations of the MCP tools exposed by Qilin.

These functions are wired into a ``FastMCP`` instance in :mod:`qilin.server`.
Keeping them as plain async functions makes them straightforward to unit-test
without involving the MCP transport layer.
"""

from __future__ import annotations

import logging
from typing import Any

from .chunking import chunk_text
from .config import get_settings
from .embeddings import EmbedTask, get_embedder
from .store import (
    _content_hash,
    build_payload,
    deterministic_point_id,
    get_store,
)

logger = logging.getLogger(__name__)


def _resolve_collection(collection: str | None) -> str:
    return collection or get_settings().default_collection


async def remember(
    text: str,
    collection: str | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Chunk ``text``, embed each chunk, and upsert them into the vector store.

    Args:
        text: Raw text to store. May be arbitrarily long; it will be chunked to
            fit the embedder's context window.
        collection: Memory namespace. Defaults to ``DEFAULT_COLLECTION``.
        metadata: Optional arbitrary key/value metadata stored on every chunk.
        source: Logical identifier of the document (e.g. file path or URL).
            Used together with the content hash to derive stable chunk IDs, so
            re-ingesting the same text under the same source is idempotent.

    Returns:
        A dict with the destination ``collection``, the number of
        ``chunks_written``, and the list of resulting point ``ids``.
    """
    if not text or not text.strip():
        return {"collection": _resolve_collection(collection), "chunks_written": 0, "ids": []}

    collection_name = _resolve_collection(collection)
    chunks = chunk_text(text)
    if not chunks:
        return {"collection": collection_name, "chunks_written": 0, "ids": []}

    embedder = await get_embedder()
    store = await get_store()

    document_hash = _content_hash(text)
    chunk_texts = [c.text for c in chunks]
    vectors = await embedder.embed(chunk_texts, task=EmbedTask.DOCUMENT)

    ids: list[str] = []
    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        point_id = deterministic_point_id(source, document_hash, chunk.ordinal)
        ids.append(point_id)
        payloads.append(
            build_payload(
                chunk_text=chunk.text,
                chunk_ordinal=chunk.ordinal,
                chunk_count=len(chunks),
                document_hash=document_hash,
                source=source,
                extra=metadata,
            )
        )

    written = await store.upsert_chunks(collection_name, vectors, payloads, ids)
    logger.info(
        "remember: collection=%s source=%s chunks=%d",
        collection_name,
        source,
        written,
    )
    return {
        "collection": collection_name,
        "chunks_written": written,
        "ids": ids,
        "document_hash": document_hash,
    }


async def recall(
    query: str,
    collection: str | None = None,
    top_k: int = 5,
    filter: dict[str, Any] | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Vector-search for the chunks most relevant to ``query``.

    Args:
        query: Natural-language query.
        collection: Memory namespace to search.
        top_k: Maximum number of hits to return.
        filter: Optional ``{field: value}`` payload filter (values may also be
            lists for match-any). Pass ``{"__raw__": {...}}`` for a
            Qdrant-native filter dict.
        score_threshold: Drop hits below this cosine similarity.

    Returns:
        A list of hits ordered by descending score; each hit includes ``id``,
        ``score``, ``text``, and the full payload.
    """
    collection_name = _resolve_collection(collection)
    if not query or not query.strip():
        return []
    if top_k <= 0:
        return []

    embedder = await get_embedder()
    store = await get_store()

    vectors = await embedder.embed([query], task=EmbedTask.QUERY)
    if not vectors:
        return []
    hits = await store.search(
        collection=collection_name,
        vector=vectors[0],
        top_k=top_k,
        filter_obj=filter,
        score_threshold=score_threshold,
    )
    return [
        {
            "id": h.id,
            "score": h.score,
            "text": h.text,
            "metadata": {k: v for k, v in h.payload.items() if k != "text"},
        }
        for h in hits
    ]


async def forget(
    ids: list[str] | None = None,
    collection: str | None = None,
    filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete points by id or by payload filter."""
    collection_name = _resolve_collection(collection)
    if not ids and not filter:
        return {"collection": collection_name, "deleted": 0, "error": "ids or filter required"}
    store = await get_store()
    deleted = await store.delete(collection_name, ids=ids, filter_obj=filter)
    return {"collection": collection_name, "deleted": deleted}


async def list_collections() -> list[str]:
    """Return the names of all collections in the vector store."""
    store = await get_store()
    return await store.list_collections()


async def create_collection(name: str) -> dict[str, Any]:
    """Create a new collection. Idempotent: succeeds even if it already exists."""
    if not name or not name.strip():
        return {"name": name, "created": False, "error": "name required"}
    store = await get_store()
    created = await store.create_collection(name)
    return {"name": name, "created": created}


async def stats(collection: str | None = None) -> dict[str, Any]:
    """Return point count and status info for a collection."""
    collection_name = _resolve_collection(collection)
    store = await get_store()
    return await store.stats(collection_name)
