"""Qdrant client wrapper used by the MCP tools."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

POINT_ID_NAMESPACE = uuid.UUID("3f9a6e1c-2b1a-4b8a-9a4c-7f1d3c2e8f01")


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A chunk that has been (or will be) persisted into Qdrant."""

    id: str
    text: str
    ordinal: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    score: float
    text: str
    payload: dict[str, Any]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deterministic_point_id(source: str | None, document_hash: str, ordinal: int) -> str:
    """Stable UUID derived from source + document hash + ordinal.

    Re-ingesting the same text under the same ``source`` is idempotent: the
    upsert overwrites prior points with the same id rather than duplicating
    them.
    """
    seed = f"{source or ''}::{document_hash}::{ordinal}"
    return str(uuid.uuid5(POINT_ID_NAMESPACE, seed))


def _build_filter(filter_obj: dict[str, Any] | None) -> qm.Filter | None:
    """Build a Qdrant ``Filter`` from a simple ``{field: value}`` dict.

    Each key becomes a ``must`` match. Values may be primitives (exact match)
    or lists (match-any). For richer filtering, callers can pass a
    Qdrant-native filter dict under the special key ``"__raw__"``.
    """
    if not filter_obj:
        return None
    if "__raw__" in filter_obj:
        return qm.Filter(**filter_obj["__raw__"])

    must: list[qm.FieldCondition] = []
    for key, value in filter_obj.items():
        if isinstance(value, list):
            must.append(qm.FieldCondition(key=key, match=qm.MatchAny(any=value)))
        else:
            must.append(qm.FieldCondition(key=key, match=qm.MatchValue(value=value)))
    return qm.Filter(must=must)


class VectorStore:
    """Async facade over Qdrant tailored to Qilin's memory model."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = AsyncQdrantClient(
            url=self._settings.qdrant_url,
            api_key=self._settings.qdrant_api_key or None,
            prefer_grpc=False,
            timeout=self._settings.http_timeout_seconds,
        )
        self._ensured: set[str] = set()
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.close()

    async def ensure_collection(self, name: str) -> None:
        """Create the collection on first use; subsequent calls are no-ops."""
        if name in self._ensured:
            return
        async with self._lock:
            if name in self._ensured:
                return
            exists = await self._client.collection_exists(collection_name=name)
            if not exists:
                logger.info("Creating Qdrant collection %r (dim=%d)", name, self._settings.embedding_dim)
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=qm.VectorParams(
                        size=self._settings.embedding_dim,
                        distance=qm.Distance.COSINE,
                    ),
                )
                await self._client.create_payload_index(
                    collection_name=name,
                    field_name="source",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
                await self._client.create_payload_index(
                    collection_name=name,
                    field_name="document_hash",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
            self._ensured.add(name)

    async def upsert_chunks(
        self,
        collection: str,
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        ids: list[str],
    ) -> int:
        if not (len(vectors) == len(payloads) == len(ids)):
            raise ValueError("vectors, payloads, and ids must have the same length")
        if not vectors:
            return 0
        await self.ensure_collection(collection)
        points = [
            qm.PointStruct(id=pid, vector=vec, payload=payload)
            for pid, vec, payload in zip(ids, vectors, payloads, strict=True)
        ]
        await self._client.upsert(collection_name=collection, points=points, wait=True)
        return len(points)

    async def search(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filter_obj: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[SearchHit]:
        try:
            response = await self._client.query_points(
                collection_name=collection,
                query=vector,
                limit=top_k,
                query_filter=_build_filter(filter_obj),
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return []
            raise

        hits: list[SearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    id=str(point.id),
                    score=float(point.score),
                    text=payload.get("text", ""),
                    payload=payload,
                )
            )
        return hits

    async def delete(
        self,
        collection: str,
        ids: list[str] | None = None,
        filter_obj: dict[str, Any] | None = None,
    ) -> int:
        if not ids and not filter_obj:
            raise ValueError("delete requires either ids or filter_obj")

        if ids:
            await self._client.delete(
                collection_name=collection,
                points_selector=qm.PointIdsList(points=list(ids)),
                wait=True,
            )
            return len(ids)

        filt = _build_filter(filter_obj)
        if filt is None:
            return 0
        before = await self.count(collection, filter_obj=filter_obj)
        await self._client.delete(
            collection_name=collection,
            points_selector=qm.FilterSelector(filter=filt),
            wait=True,
        )
        return before

    async def count(
        self,
        collection: str,
        filter_obj: dict[str, Any] | None = None,
    ) -> int:
        try:
            result = await self._client.count(
                collection_name=collection,
                count_filter=_build_filter(filter_obj),
                exact=True,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return 0
            raise
        return int(result.count)

    async def chunks_exist(
        self,
        collection: str,
        source: str,
        document_hash: str,
    ) -> bool:
        """Return True iff at least one chunk for ``(source, document_hash)`` already lives in ``collection``.

        Used by the CLI ingester for resumability: if the same file content has
        already been stored under the same logical source, skip re-embedding it.
        """
        try:
            exists = await self._client.collection_exists(collection_name=collection)
        except UnexpectedResponse:
            return False
        if not exists:
            return False
        n = await self.count(
            collection,
            filter_obj={"source": source, "document_hash": document_hash},
        )
        return n > 0

    async def list_collections(self) -> list[str]:
        collections = await self._client.get_collections()
        return sorted(c.name for c in collections.collections)

    async def create_collection(self, name: str) -> bool:
        """Create a collection if missing; return True iff it was newly created."""
        if await self._client.collection_exists(collection_name=name):
            self._ensured.add(name)
            return False
        await self.ensure_collection(name)
        return True

    async def stats(self, collection: str) -> dict[str, Any]:
        if not await self._client.collection_exists(collection_name=collection):
            return {"collection": collection, "exists": False}
        info = await self._client.get_collection(collection_name=collection)
        return {
            "collection": collection,
            "exists": True,
            "points_count": getattr(info, "points_count", None),
            "vectors_count": getattr(info, "vectors_count", None),
            "indexed_vectors_count": getattr(info, "indexed_vectors_count", None),
            "status": getattr(info.status, "value", str(info.status)) if info.status else None,
        }

    async def health(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False


def build_payload(
    *,
    chunk_text: str,
    chunk_ordinal: int,
    chunk_count: int,
    document_hash: str,
    source: str | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": chunk_text,
        "chunk_ordinal": chunk_ordinal,
        "chunk_count": chunk_count,
        "document_hash": document_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if source is not None:
        payload["source"] = source
    if extra:
        for key, value in extra.items():
            if key in payload:
                continue
            payload[key] = value
    return payload


_store_lock = asyncio.Lock()
_store: VectorStore | None = None


async def get_store() -> VectorStore:
    global _store
    async with _store_lock:
        if _store is None:
            _store = VectorStore()
        return _store


async def shutdown_store() -> None:
    global _store
    async with _store_lock:
        if _store is not None:
            await _store.aclose()
            _store = None


__all__ = [
    "POINT_ID_NAMESPACE",
    "SearchHit",
    "StoredChunk",
    "VectorStore",
    "_content_hash",
    "build_payload",
    "deterministic_point_id",
    "get_store",
    "shutdown_store",
]
