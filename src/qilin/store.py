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
from .sparse import SparseVector

logger = logging.getLogger(__name__)

POINT_ID_NAMESPACE = uuid.UUID("3f9a6e1c-2b1a-4b8a-9a4c-7f1d3c2e8f01")

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"


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
    vector: list[float] | None = None


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
        """Create the collection on first use; subsequent calls are no-ops.

        When :attr:`Settings.hybrid_enabled` is True, new collections are
        created with named dense + BM25 sparse vectors so :meth:`search` can
        run hybrid queries. Otherwise the legacy single-unnamed-vector layout
        is used (full backward compatibility with pre-1.1 setups).
        """
        if name in self._ensured:
            return
        async with self._lock:
            if name in self._ensured:
                return
            exists = await self._client.collection_exists(collection_name=name)
            if not exists:
                if self._settings.hybrid_enabled:
                    logger.info(
                        "Creating hybrid Qdrant collection %r (dim=%d)",
                        name,
                        self._settings.embedding_dim,
                    )
                    await self._client.create_collection(
                        collection_name=name,
                        vectors_config={
                            DENSE_VECTOR_NAME: qm.VectorParams(
                                size=self._settings.embedding_dim,
                                distance=qm.Distance.COSINE,
                            ),
                        },
                        sparse_vectors_config={
                            SPARSE_VECTOR_NAME: qm.SparseVectorParams(
                                modifier=qm.Modifier.IDF,
                            ),
                        },
                    )
                else:
                    logger.info(
                        "Creating Qdrant collection %r (dim=%d)",
                        name,
                        self._settings.embedding_dim,
                    )
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
                await self._client.create_payload_index(
                    collection_name=name,
                    field_name="defines",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
                await self._client.create_payload_index(
                    collection_name=name,
                    field_name="language",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
            self._ensured.add(name)

    async def upsert_chunks(
        self,
        collection: str,
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        ids: list[str],
        sparse_vectors: list[SparseVector] | None = None,
    ) -> int:
        if not (len(vectors) == len(payloads) == len(ids)):
            raise ValueError("vectors, payloads, and ids must have the same length")
        if sparse_vectors is not None and len(sparse_vectors) != len(vectors):
            raise ValueError("sparse_vectors must match vectors length")
        if not vectors:
            return 0
        await self.ensure_collection(collection)

        points: list[qm.PointStruct] = []
        if self._settings.hybrid_enabled:
            for i, (pid, vec, payload) in enumerate(
                zip(ids, vectors, payloads, strict=True)
            ):
                point_vec: dict[str, Any] = {DENSE_VECTOR_NAME: vec}
                if sparse_vectors is not None:
                    sv = sparse_vectors[i]
                    point_vec[SPARSE_VECTOR_NAME] = qm.SparseVector(
                        indices=sv.indices, values=sv.values
                    )
                points.append(qm.PointStruct(id=pid, vector=point_vec, payload=payload))
        else:
            for pid, vec, payload in zip(ids, vectors, payloads, strict=True):
                points.append(qm.PointStruct(id=pid, vector=vec, payload=payload))

        await self._client.upsert(collection_name=collection, points=points, wait=True)
        return len(points)

    async def search(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filter_obj: dict[str, Any] | None = None,
        score_threshold: float | None = None,
        *,
        with_vectors: bool = False,
        mode: str = "dense",
        sparse_vector: SparseVector | None = None,
    ) -> list[SearchHit]:
        """Vector search with optional sparse / hybrid modes.

        ``mode`` is one of:

        - ``"dense"`` - cosine similarity on the dense vector (the default).
        - ``"sparse"`` - BM25 over the sparse vector. Requires
          ``hybrid_enabled`` and a ``sparse_vector`` argument; otherwise the
          search silently degrades to dense.
        - ``"hybrid"`` - both queries run via Qdrant's RRF fusion. Same
          fallback behavior as ``sparse``.
        """
        effective_mode = mode
        hybrid_ready = (
            self._settings.hybrid_enabled
            and sparse_vector is not None
            and (sparse_vector.indices or sparse_vector.values)
        )
        if effective_mode in ("sparse", "hybrid") and not hybrid_ready:
            effective_mode = "dense"

        query_filter = _build_filter(filter_obj)

        try:
            if effective_mode == "hybrid":
                response = await self._client.query_points(
                    collection_name=collection,
                    prefetch=[
                        qm.Prefetch(
                            query=vector,
                            using=DENSE_VECTOR_NAME,
                            limit=max(top_k * 2, top_k),
                            filter=query_filter,
                        ),
                        qm.Prefetch(
                            query=qm.SparseVector(
                                indices=sparse_vector.indices,  # type: ignore[union-attr]
                                values=sparse_vector.values,  # type: ignore[union-attr]
                            ),
                            using=SPARSE_VECTOR_NAME,
                            limit=max(top_k * 2, top_k),
                            filter=query_filter,
                        ),
                    ],
                    query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                    limit=top_k,
                    query_filter=query_filter,
                    score_threshold=score_threshold,
                    with_payload=True,
                    with_vectors=with_vectors,
                )
            elif effective_mode == "sparse":
                response = await self._client.query_points(
                    collection_name=collection,
                    query=qm.SparseVector(
                        indices=sparse_vector.indices,  # type: ignore[union-attr]
                        values=sparse_vector.values,  # type: ignore[union-attr]
                    ),
                    using=SPARSE_VECTOR_NAME,
                    limit=top_k,
                    query_filter=query_filter,
                    score_threshold=score_threshold,
                    with_payload=True,
                    with_vectors=with_vectors,
                )
            else:
                kwargs: dict[str, Any] = {
                    "collection_name": collection,
                    "query": vector,
                    "limit": top_k,
                    "query_filter": query_filter,
                    "score_threshold": score_threshold,
                    "with_payload": True,
                    "with_vectors": with_vectors,
                }
                if self._settings.hybrid_enabled:
                    kwargs["using"] = DENSE_VECTOR_NAME
                response = await self._client.query_points(**kwargs)
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return []
            raise

        hits: list[SearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            raw_vec = getattr(point, "vector", None) if with_vectors else None
            vec: list[float] | None = None
            if raw_vec is not None:
                if isinstance(raw_vec, dict):
                    candidate = raw_vec.get(DENSE_VECTOR_NAME) or next(
                        iter(raw_vec.values()), None
                    )
                    if isinstance(candidate, list):
                        vec = [float(x) for x in candidate]
                elif isinstance(raw_vec, list):
                    vec = [float(x) for x in raw_vec]
            hits.append(
                SearchHit(
                    id=str(point.id),
                    score=float(point.score),
                    text=payload.get("text", ""),
                    payload=payload,
                    vector=vec,
                )
            )
        return hits

    async def fetch_neighbors(
        self,
        collection: str,
        source: str,
        ordinals: list[int],
    ) -> list[dict[str, Any]]:
        """Return payloads of chunks matching ``source`` and any of ``ordinals``.

        Used by :func:`tools.recall`'s neighbor-expansion path. The result is
        sorted by ``chunk_ordinal`` ascending so callers can merge adjacent
        chunks into one contiguous citation.
        """
        if not ordinals:
            return []
        try:
            points, _ = await self._client.scroll(
                collection_name=collection,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="source", match=qm.MatchValue(value=source)
                        ),
                        qm.FieldCondition(
                            key="chunk_ordinal",
                            match=qm.MatchAny(any=list(ordinals)),
                        ),
                    ]
                ),
                with_payload=True,
                with_vectors=False,
                limit=len(ordinals),
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return []
            raise
        payloads = [(p.payload or {}) for p in points]
        payloads.sort(key=lambda p: p.get("chunk_ordinal", 0))
        return payloads

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

    async def bump_feedback(self, collection: str, point_id: str, delta: int) -> int:
        """Add ``delta`` to a point's ``feedback`` payload field; return the new value.

        Used by the ``mark_useful`` MCP tool. Reads the current payload, bumps
        the ``feedback`` integer (default 0), and writes it back via
        ``set_payload``. The score boost in :func:`tools.recall` reads this
        field on subsequent recalls.
        """
        try:
            records = await self._client.retrieve(
                collection_name=collection,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return 0
            raise
        if not records:
            return 0
        current = (records[0].payload or {}).get("feedback")
        new_value = (int(current) if isinstance(current, int) else 0) + int(delta)
        await self._client.set_payload(
            collection_name=collection,
            points=[point_id],
            payload={"feedback": new_value},
            wait=True,
        )
        return new_value

    async def sweep_expired(self, collection: str, *, now_iso: str) -> int:
        """Delete every chunk in ``collection`` whose ``expires_at`` is past ``now_iso``.

        Used by the TTL background sweeper. ``expires_at`` is written by
        ``tools.remember`` when the destination collection has
        ``ttl_seconds`` configured. Returns the number of points that
        matched the filter at sweep time (best-effort: counted just before
        the delete, so a concurrent write may slightly inflate the value).
        """
        try:
            exists = await self._client.collection_exists(collection_name=collection)
        except UnexpectedResponse:
            return 0
        if not exists:
            return 0
        expired_filter = qm.Filter(
            must=[
                qm.FieldCondition(
                    key="expires_at",
                    range=qm.DatetimeRange(lt=now_iso),
                )
            ]
        )
        try:
            count_res = await self._client.count(
                collection_name=collection,
                count_filter=expired_filter,
                exact=True,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return 0
            raise
        matched = int(getattr(count_res, "count", 0) or 0)
        if matched == 0:
            return 0
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=qm.FilterSelector(filter=expired_filter),
                wait=True,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return 0
            raise
        return matched

    async def scan_sources(self, collection: str) -> dict[str, list[dict[str, Any]]]:
        """Enumerate the sources currently stored in ``collection``.

        Returns ``{source: [{"document_hash": str, "chunk_count": int,
        "ids": [str, ...]}, ...]}``. A given ``source`` may appear with
        multiple hashes if prior ingests left stale points behind.

        Pages through Qdrant via ``scroll`` so the result fits in memory even
        for large collections; this is the DB-as-manifest used by incremental
        ingest.
        """
        try:
            exists = await self._client.collection_exists(collection_name=collection)
        except UnexpectedResponse:
            return {}
        if not exists:
            return {}

        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        offset: Any = None
        while True:
            try:
                points, offset = await self._client.scroll(
                    collection_name=collection,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except UnexpectedResponse as exc:
                if exc.status_code == 404:
                    return {}
                raise
            if not points:
                break
            for p in points:
                payload = p.payload or {}
                src = payload.get("source")
                hash_ = payload.get("document_hash")
                if src is None or hash_ is None:
                    continue
                by_hash = grouped.setdefault(src, {})
                entry = by_hash.setdefault(
                    hash_,
                    {
                        "document_hash": hash_,
                        "chunk_count": payload.get("chunk_count", 0),
                        "ids": [],
                    },
                )
                entry["ids"].append(str(p.id))
            if offset is None:
                break
        return {src: list(by_hash.values()) for src, by_hash in grouped.items()}

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
    start_line: int | None = None,
    end_line: int | None = None,
    defines: tuple[str, ...] | list[str] | None = None,
    imports: tuple[str, ...] | list[str] | None = None,
    signature: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Compose the Qdrant payload stored alongside each chunk vector.

    ``start_line`` and ``end_line`` (1-indexed, inclusive) are written when
    provided so callers can build ``src/foo.py:30-95``-style citations from
    recall results without re-reading the original document.

    ``defines``/``imports``/``signature``/``language`` are written by the
    code-aware chunking path so recalls can filter by symbol or scope queries
    by file language.
    """
    payload: dict[str, Any] = {
        "text": chunk_text,
        "chunk_ordinal": chunk_ordinal,
        "chunk_count": chunk_count,
        "document_hash": document_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if source is not None:
        payload["source"] = source
    if start_line is not None:
        payload["start_line"] = int(start_line)
    if end_line is not None:
        payload["end_line"] = int(end_line)
    if defines:
        payload["defines"] = list(defines)
    if imports:
        payload["imports"] = list(imports)
    if signature:
        payload["signature"] = signature
    if language:
        payload["language"] = language
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
