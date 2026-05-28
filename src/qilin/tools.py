"""Pure-Python implementations of the MCP tools exposed by Qilin.

These functions are wired into a ``FastMCP`` instance in :mod:`qilin.server`.
Keeping them as plain async functions makes them straightforward to unit-test
without involving the MCP transport layer.

Starting with v1.0.0 the ``recall`` response shape is *flat*: first-class
fields (``source``, ``language``, ``start_line``, ``end_line``, ``lines``,
``git_sha``, ``chunk_ordinal``, ``chunk_count``, ``document_hash``,
``created_at``) live at the top level alongside ``id``/``score``/``text``.
Anything user-supplied via ``remember(metadata=...)`` lives under
``extra_metadata``. See ``docs/migrating-to-1.0.md`` for the migration path.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from . import analytics
from .chunking import Chunk
from .code_chunking import chunk_code
from .config import get_settings
from .embeddings import EmbedTask, get_embedder
from .reranker import get_reranker
from .sparse import get_sparse_embedder
from .store import (
    SearchHit,
    VectorStore,
    _content_hash,
    build_payload,
    deterministic_point_id,
    get_store,
)
from .workspace_scope import resolve_scope, source_matches_workspace

logger = logging.getLogger(__name__)

# Payload keys that are promoted to the top level of every ``recall`` hit.
# Everything else falls into ``extra_metadata``.
_FIRST_CLASS_KEYS: frozenset[str] = frozenset(
    {
        "source",
        "language",
        "start_line",
        "end_line",
        "git_sha",
        "chunk_ordinal",
        "chunk_count",
        "document_hash",
        "created_at",
        "defines",
        "imports",
        "signature",
        "is_parent",
        "is_child",
        "parent_id",
        "parent_ordinal",
        "child_ordinal",
    }
)


def _resolve_collection(collection: str | None) -> str:
    return collection or get_settings().default_collection


def _format_lines(start: int | None, end: int | None) -> str | None:
    """Render an inclusive line span as a compact citation suffix.

    ``30 == start == end`` becomes ``"30"``; ``30..95`` becomes ``"30-95"``.
    Returns ``None`` when either bound is missing so the caller can omit the
    field entirely.
    """
    if start is None or end is None:
        return None
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _format_hit(hit_id: str, score: float, payload: dict[str, Any]) -> dict[str, Any]:
    """Project a Qdrant payload onto the flat recall-hit shape."""
    text = payload.get("text", "")
    out: dict[str, Any] = {"id": hit_id, "score": score, "text": text}

    for key in _FIRST_CLASS_KEYS:
        if key in payload:
            out[key] = payload[key]

    lines = _format_lines(payload.get("start_line"), payload.get("end_line"))
    if lines is not None:
        out["lines"] = lines

    extra = {
        k: v
        for k, v in payload.items()
        if k != "text" and k not in _FIRST_CLASS_KEYS
    }
    if extra:
        out["extra_metadata"] = extra

    return out


async def remember(
    text: str,
    collection: str | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
    language: str | None = None,
    workspace_roots: list[str] | None = None,
    git_branch: str | None = None,
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
        language: Optional language hint (``python``, ``go``, ``typescript``,
            ``rust``, ...). When provided and supported by the code-aware
            chunker, chunks are split on AST boundaries and carry
            ``defines``/``imports``/``signature`` metadata.

    Returns:
        A dict with the destination ``collection``, the number of
        ``chunks_written``, the resulting point ``ids``, and the
        ``document_hash``.
    """
    if not text or not text.strip():
        return {"collection": _resolve_collection(collection), "chunks_written": 0, "ids": []}

    settings = get_settings()
    collection_name = _resolve_collection(collection)
    scope = resolve_scope(
        settings=settings,
        base_collection=collection_name,
        explicit_workspace_roots=workspace_roots,
        git_branch=git_branch,
    )
    collection_name = scope.collection
    coll_settings = settings.for_collection(collection_name)
    chunks = chunk_code(text, language, settings=coll_settings)
    if not chunks:
        return {"collection": collection_name, "chunks_written": 0, "ids": []}

    embedder = await get_embedder()
    store = await get_store()

    document_hash = _content_hash(text)
    hierarchical = bool(coll_settings.parent_child_enabled)
    parent_chunks: list[Chunk] = []
    child_chunks: list[Chunk] = []
    parent_ids: list[str] = []
    child_ids: list[str] = []
    payloads: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    ids: list[str] = []

    ttl_seconds = None
    override = get_settings().collections.get(collection_name)
    if override is not None and override.ttl_seconds is not None:
        ttl_seconds = override.ttl_seconds
    expires_at = (
        (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        if ttl_seconds is not None
        else None
    )

    if hierarchical:
        parent_chunks = chunk_code(
            text,
            language,
            chunk_size_tokens=coll_settings.parent_chunk_size_tokens,
            chunk_overlap_tokens=coll_settings.parent_chunk_overlap_tokens,
            settings=coll_settings,
        )
        child_triplets: list[tuple[str, int, Chunk]] = []
        for parent in parent_chunks:
            parent_id = deterministic_point_id(
                source,
                document_hash,
                parent.ordinal,
                point_kind="parent",
            )
            parent_ids.append(parent_id)
            local_children = chunk_code(
                parent.text,
                language,
                chunk_size_tokens=coll_settings.child_chunk_size_tokens,
                chunk_overlap_tokens=coll_settings.child_chunk_overlap_tokens,
                settings=coll_settings,
            )
            for local_child in local_children:
                adjusted_child = Chunk(
                    text=local_child.text,
                    ordinal=len(child_chunks),
                    token_count=local_child.token_count,
                    start_line=parent.start_line + local_child.start_line - 1,
                    end_line=parent.start_line + local_child.end_line - 1,
                    defines=local_child.defines,
                    imports=local_child.imports,
                    signature=local_child.signature,
                )
                child_chunks.append(adjusted_child)
                child_triplets.append((parent_id, parent.ordinal, local_child))

        parent_vectors = await embedder.embed(
            [chunk.text for chunk in parent_chunks], task=EmbedTask.DOCUMENT
        )
        child_vectors = await embedder.embed(
            [chunk.text for chunk in child_chunks], task=EmbedTask.DOCUMENT
        )
        vectors.extend(parent_vectors)
        vectors.extend(child_vectors)

        for parent in parent_chunks:
            point_id = deterministic_point_id(
                source,
                document_hash,
                parent.ordinal,
                point_kind="parent",
            )
            ids.append(point_id)
            chunk_extra = dict(metadata) if metadata else {}
            if expires_at is not None:
                chunk_extra["expires_at"] = expires_at
            payloads.append(
                build_payload(
                    chunk_text=parent.text,
                    chunk_ordinal=parent.ordinal,
                    chunk_count=len(parent_chunks),
                    document_hash=document_hash,
                    source=source,
                    extra=chunk_extra or None,
                    start_line=parent.start_line,
                    end_line=parent.end_line,
                    defines=parent.defines or None,
                    imports=parent.imports or None,
                    signature=parent.signature,
                    language=language,
                    hierarchy={
                        "is_parent": True,
                        "is_child": False,
                        "parent_id": point_id,
                        "parent_ordinal": parent.ordinal,
                    },
                )
            )
        for parent_id, parent_ordinal, child in child_triplets:
            point_id = deterministic_point_id(
                source,
                document_hash,
                child.ordinal,
                point_kind="child",
                parent_ordinal=parent_ordinal,
            )
            child_ids.append(point_id)
            ids.append(point_id)
            chunk_extra = dict(metadata) if metadata else {}
            if expires_at is not None:
                chunk_extra["expires_at"] = expires_at
            payloads.append(
                build_payload(
                    chunk_text=child.text,
                    chunk_ordinal=child.ordinal,
                    chunk_count=len(child_chunks),
                    document_hash=document_hash,
                    source=source,
                    extra=chunk_extra or None,
                    start_line=child.start_line,
                    end_line=child.end_line,
                    defines=child.defines or None,
                    imports=child.imports or None,
                    signature=child.signature,
                    language=language,
                    hierarchy={
                        "is_parent": False,
                        "is_child": True,
                        "parent_id": parent_id,
                        "child_ordinal": child.ordinal,
                        "parent_ordinal": parent_ordinal,
                    },
                )
            )
    else:
        chunk_texts = [c.text for c in chunks]
        vectors = await embedder.embed(chunk_texts, task=EmbedTask.DOCUMENT)
        for chunk in chunks:
            point_id = deterministic_point_id(source, document_hash, chunk.ordinal)
            ids.append(point_id)
            chunk_extra = dict(metadata) if metadata else {}
            if expires_at is not None:
                chunk_extra["expires_at"] = expires_at
            payloads.append(
                build_payload(
                    chunk_text=chunk.text,
                    chunk_ordinal=chunk.ordinal,
                    chunk_count=len(chunks),
                    document_hash=document_hash,
                    source=source,
                    extra=chunk_extra or None,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    defines=chunk.defines or None,
                    imports=chunk.imports or None,
                    signature=chunk.signature,
                    language=language,
                )
            )

    sparse_vectors = None
    if get_settings().hybrid_enabled:
        sparse_embedder = await get_sparse_embedder()
        sparse_vectors = sparse_embedder.embed([p["text"] for p in payloads])

    written = await store.upsert_chunks(
        collection_name, vectors, payloads, ids, sparse_vectors=sparse_vectors
    )
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
        "parent_chunks_written": len(parent_chunks),
        "child_chunks_written": len(child_ids) if hierarchical else len(ids),
    }


async def _promote_child_hits_to_parents(
    store: VectorStore,
    collection_name: str,
    hits: list[SearchHit],
    top_k: int,
) -> list[SearchHit]:
    if not hits:
        return []
    best_by_parent: dict[str, SearchHit] = {}
    ordered_parent_ids: list[str] = []
    for hit in hits:
        parent_id = hit.payload.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            continue
        current = best_by_parent.get(parent_id)
        if current is None:
            best_by_parent[parent_id] = hit
            ordered_parent_ids.append(parent_id)
            continue
        if hit.score > current.score:
            best_by_parent[parent_id] = hit
    parent_payloads = await store.fetch_payloads_by_ids(collection_name, ordered_parent_ids)
    promoted: list[SearchHit] = []
    for parent_id in ordered_parent_ids:
        child = best_by_parent[parent_id]
        parent_payload = parent_payloads.get(parent_id)
        if not parent_payload:
            continue
        payload = dict(parent_payload)
        payload["matched_child_id"] = child.id
        if isinstance(child.payload.get("child_ordinal"), int):
            payload["matched_child_ordinal"] = child.payload["child_ordinal"]
        promoted.append(
            SearchHit(
                id=parent_id,
                score=child.score,
                text=payload.get("text", ""),
                payload=payload,
                vector=None,
            )
        )
        if len(promoted) >= top_k:
            break
    return promoted


def _cosine(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity. Returns 0.0 on a zero-magnitude input."""
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _mmr_rerank(
    query_vec: list[float],
    hits: list[SearchHit],
    lambda_: float,
    top_k: int,
) -> list[SearchHit]:
    """Re-rank ``hits`` using Maximal Marginal Relevance.

    Each iteration picks the candidate that maximizes
    ``lambda_ * sim(query, c) - (1 - lambda_) * max_{s in selected} sim(c, s)``.
    Hits without a stored vector are appended to the tail in their original order
    so the function degrades gracefully if the caller forgot ``with_vectors``.
    """
    if not hits or top_k <= 0:
        return []
    lambda_ = max(0.0, min(1.0, lambda_))
    vectored = [h for h in hits if h.vector is not None]
    plain = [h for h in hits if h.vector is None]
    if not vectored:
        return hits[:top_k]

    sim_to_query = {h.id: _cosine(query_vec, h.vector or []) for h in vectored}
    selected: list[SearchHit] = []
    remaining = list(vectored)
    while remaining and len(selected) < top_k:
        best: SearchHit | None = None
        best_score = -math.inf
        for h in remaining:
            if not selected:
                score = sim_to_query[h.id]
            else:
                max_sim = max(
                    _cosine(h.vector or [], s.vector or []) for s in selected
                )
                score = lambda_ * sim_to_query[h.id] - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best = h
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)

    if len(selected) < top_k and plain:
        selected.extend(plain[: top_k - len(selected)])
    return selected


def _group_by_source(hits: list[SearchHit]) -> list[SearchHit]:
    """Keep the highest-scoring hit per ``source``.

    Hits with no ``source`` in their payload pass through unchanged - they
    cannot collide with each other.
    """
    seen: dict[str, SearchHit] = {}
    unsourced: list[SearchHit] = []
    for h in hits:
        src = h.payload.get("source")
        if src is None:
            unsourced.append(h)
            continue
        if src not in seen:
            seen[src] = h
    return list(seen.values()) + unsourced


def _merge_siblings(
    siblings: list[dict[str, Any]],
    anchor: dict[str, Any],
) -> dict[str, Any]:
    """Merge sibling payloads (already sorted by ``chunk_ordinal``) into ``anchor``.

    The anchor keeps its ``id`` and ``score`` (so MMR / threshold semantics
    don't get scrambled), but its ``text`` and line span are replaced by the
    union of its siblings.
    """
    if not siblings:
        return anchor

    out = dict(anchor)
    out["text"] = "\n\n".join(s.get("text", "") for s in siblings if s.get("text"))

    starts = [s["start_line"] for s in siblings if "start_line" in s]
    ends = [s["end_line"] for s in siblings if "end_line" in s]
    if starts:
        out["start_line"] = min(starts)
    if ends:
        out["end_line"] = max(ends)
    lines = _format_lines(out.get("start_line"), out.get("end_line"))
    if lines is not None:
        out["lines"] = lines

    ordinals = [s["chunk_ordinal"] for s in siblings if "chunk_ordinal" in s]
    if ordinals:
        out["chunk_ordinals"] = ordinals
    return out


async def _expand_neighbors(
    store: VectorStore,
    collection: str,
    hits: list[dict[str, Any]],
    window: int,
) -> list[dict[str, Any]]:
    """Fetch +/- ``window`` sibling chunks per hit and merge them in-place.

    Hits whose neighborhood overlaps an earlier hit's expanded range are
    dropped to avoid returning near-duplicate context blocks.
    """
    if window <= 0:
        return hits
    out: list[dict[str, Any]] = []
    seen_ranges: dict[str, list[tuple[int, int]]] = {}
    for hit in hits:
        src = hit.get("source")
        ord_ = hit.get("chunk_ordinal")
        if not isinstance(src, str) or not isinstance(ord_, int):
            out.append(hit)
            continue
        ranges = seen_ranges.setdefault(src, [])
        if any(s <= ord_ <= e for s, e in ranges):
            continue
        target = list(range(max(0, ord_ - window), ord_ + window + 1))
        siblings = await store.fetch_neighbors(collection, src, target)
        if not siblings:
            out.append(hit)
            continue
        merged = _merge_siblings(siblings, hit)
        out.append(merged)
        ordinals = [
            s["chunk_ordinal"] for s in siblings if isinstance(s.get("chunk_ordinal"), int)
        ]
        if ordinals:
            ranges.append((min(ordinals), max(ordinals)))
    return out


async def _rerank_hits(query: str, hits: list[SearchHit]) -> list[SearchHit]:
    """Re-rank ``hits`` by a cross-encoder. No-op when the reranker is unavailable."""
    if not hits:
        return hits
    reranker = await get_reranker()
    if not reranker.available:
        return hits
    docs = [h.text for h in hits]
    scores = reranker.score(query, docs)
    if scores is None or len(scores) != len(hits):
        return hits
    reordered = sorted(
        zip(hits, scores, strict=True),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [
        SearchHit(
            id=h.id,
            score=float(s),
            text=h.text,
            payload=h.payload,
            vector=h.vector,
        )
        for h, s in reordered
    ]


def _resolve_mode(mode: str | None) -> str:
    if mode is None:
        return "hybrid" if get_settings().hybrid_enabled else "dense"
    mode = mode.lower()
    if mode not in {"dense", "sparse", "hybrid"}:
        raise ValueError(f"unknown recall mode {mode!r}")
    return mode


async def recall(
    query: str,
    collection: str | None = None,
    top_k: int = 5,
    filter: dict[str, Any] | None = None,
    score_threshold: float | None = None,
    *,
    context_window: int = 0,
    group_by_source: bool = False,
    mmr_lambda: float | None = None,
    mode: str | None = None,
    rerank: bool | None = None,
    rerank_top_k: int | None = None,
    workspace_roots: list[str] | None = None,
    git_branch: str | None = None,
    fallback_strategy: str | None = None,
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
        context_window: When > 0, fetch +/- N sibling chunks per hit (same
            ``source``) and merge them into one contiguous text block.
        group_by_source: When True, return at most one hit per ``source``.
        mmr_lambda: When set (typically ``0.5`` - ``0.8``), re-rank the
            initial candidate pool with Maximal Marginal Relevance.
        mode: ``"dense"`` | ``"sparse"`` | ``"hybrid"``. Defaults to
            ``hybrid`` when ``hybrid_enabled`` is on, otherwise ``dense``.
            Sparse/hybrid modes silently degrade to dense if hybrid is
            unavailable for this collection.
        rerank: When True (or unset and ``rerank_enabled`` is on), the
            initial candidate pool is reordered by a cross-encoder
            cross-encoder. Falls back to vector order if the model fails to
            load.
        rerank_top_k: Candidate pool size to feed the reranker. Defaults to
            ``settings.rerank_top_k`` (50).

    Returns:
        A list of hits ordered by descending score. Each hit is a flat dict
        with ``id``, ``score``, ``text``, and the first-class payload fields
        promoted to the top level. Neighbor-expanded hits also carry
        ``chunk_ordinals``.
    """
    start_ms = analytics.Clock.now_ms()
    collection_name = _resolve_collection(collection)
    if not query or not query.strip():
        return []
    if top_k <= 0:
        return []

    settings = get_settings()
    scope = resolve_scope(
        settings=settings,
        base_collection=collection_name,
        explicit_workspace_roots=workspace_roots,
        git_branch=git_branch,
    )
    collection_name = scope.collection
    strategy = fallback_strategy or settings.branch_fallback_strategy
    candidate_collections = list(scope.recall_collections or [collection_name])
    effective_mode = _resolve_mode(mode)
    rerank_on = settings.rerank_enabled if rerank is None else bool(rerank)
    rerank_pool = max(top_k, rerank_top_k or settings.rerank_top_k)

    embedder = await get_embedder()
    store = await get_store()

    vectors = await embedder.embed([query], task=EmbedTask.QUERY)
    if not vectors:
        return []

    sparse_query = None
    if effective_mode in {"sparse", "hybrid"} and settings.hybrid_enabled:
        sparse_embedder = await get_sparse_embedder()
        sparse_query = sparse_embedder.embed_one(query)
        if sparse_query is None:
            effective_mode = "dense"

    needs_post = mmr_lambda is not None or group_by_source or rerank_on
    internal_k = max(top_k * 4, 25, rerank_pool) if needs_post else top_k
    if scope.apply_prefix_filter and scope.workspace_roots:
        internal_k = max(internal_k, top_k * 20)
    need_vectors = mmr_lambda is not None

    async def _search_collection(target_collection: str) -> list[SearchHit]:
        coll_settings = settings.for_collection(target_collection)
        return await store.search(
            collection=target_collection,
            vector=vectors[0],
            top_k=internal_k,
            filter_obj=(
                {"is_child": True, **(filter or {})}
                if coll_settings.parent_child_enabled
                else filter
            ),
            score_threshold=score_threshold,
            with_vectors=need_vectors,
            mode=effective_mode,
            sparse_vector=sparse_query,
        )

    searched_collections: list[str] = []
    per_collection_hits: list[tuple[str, list[SearchHit]]] = []
    if strategy == "active_then_baseline" and len(candidate_collections) > 1:
        first_collection = candidate_collections[0]
        first_hits = await _search_collection(first_collection)
        searched_collections.append(first_collection)
        per_collection_hits.append((first_collection, first_hits))
        if len(first_hits) < top_k:
            for extra_collection in candidate_collections[1:]:
                extra_hits = await _search_collection(extra_collection)
                searched_collections.append(extra_collection)
                per_collection_hits.append((extra_collection, extra_hits))
    else:
        for target_collection in candidate_collections:
            found = await _search_collection(target_collection)
            searched_collections.append(target_collection)
            per_collection_hits.append((target_collection, found))

    merged: list[SearchHit] = []
    dedup_ids: set[str] = set()
    for found_hits in (hits for _, hits in per_collection_hits):
        ordered_hits = sorted(found_hits, key=lambda h: h.score, reverse=True)
        for hit in ordered_hits:
            if hit.id in dedup_ids:
                continue
            dedup_ids.add(hit.id)
            merged.append(
                SearchHit(
                    id=hit.id,
                    score=float(hit.score),
                    text=hit.text,
                    payload=dict(hit.payload),
                    vector=hit.vector,
                )
            )
    hits = merged

    if rerank_on:
        hits = await _rerank_hits(query, hits[:rerank_pool])
    hits = analytics.apply_feedback(hits)
    if mmr_lambda is not None:
        hits = _mmr_rerank(vectors[0], hits, mmr_lambda, internal_k)
    if group_by_source:
        hits = _group_by_source(hits)
    if scope.apply_prefix_filter and scope.workspace_roots:
        hits = [
            h
            for h in hits
            if source_matches_workspace(
                h.payload.get("source"), scope.workspace_roots
            )
        ]
    if settings.for_collection(collection_name).parent_child_enabled:
        hits = await _promote_child_hits_to_parents(store, collection_name, hits, top_k)
    else:
        hits = hits[:top_k]

    formatted = [_format_hit(h.id, h.score, h.payload) for h in hits]

    if context_window > 0 and not settings.for_collection(collection_name).parent_child_enabled:
        formatted = await _expand_neighbors(store, collection_name, formatted, context_window)

    elapsed_ms = analytics.Clock.now_ms() - start_ms
    log_path = analytics.resolve_log_path(settings.recall_log_path)
    analytics.log_recall(
        log_path,
        query=query,
        collection=",".join(searched_collections) if searched_collections else collection_name,
        top_k=top_k,
        mode=effective_mode,
        rerank=rerank_on,
        latency_ms=elapsed_ms,
        hits=formatted,
    )

    return formatted


async def recall_files(
    query: str,
    collection: str | None = None,
    top_k: int = 5,
    filter: dict[str, Any] | None = None,
    score_threshold: float | None = None,
    *,
    mode: str | None = None,
    rerank: bool | None = None,
    workspace_roots: list[str] | None = None,
    git_branch: str | None = None,
    fallback_strategy: str | None = None,
) -> list[dict[str, Any]]:
    """Return the top-K ``source`` files for ``query``.

    Pulls a larger pool of chunk hits than ``top_k``, groups by ``source``,
    sums scores per file, and returns one entry per source ordered by total
    score descending. Each entry carries:

    - ``source`` - the file path / logical identifier.
    - ``score`` - the summed hit score (use as a relative ranking only).
    - ``top_score`` - the single best chunk score within the file.
    - ``hit_count`` - how many chunks of the file made the cut.
    - ``lines`` - the line span of the best chunk (or absent).
    - ``preview`` - the first ~240 chars of the best chunk.
    - ``language`` - mirrored from the best chunk when present.

    This is the right tool for "which files are relevant?" style queries -
    far cheaper for the calling LLM than asking it to dedupe a chunk list.
    """
    if not query or not query.strip() or top_k <= 0:
        return []

    chunk_pool = max(top_k * 5, 25)
    hits = await recall(
        query=query,
        collection=collection,
        top_k=chunk_pool,
        filter=filter,
        score_threshold=score_threshold,
        mode=mode,
        rerank=rerank,
        workspace_roots=workspace_roots,
        git_branch=git_branch,
        fallback_strategy=fallback_strategy,
    )

    by_source: dict[str, dict[str, Any]] = {}
    for h in hits:
        source = h.get("source")
        if not source:
            continue
        entry = by_source.get(source)
        if entry is None:
            entry = {
                "source": source,
                "score": 0.0,
                "top_score": 0.0,
                "hit_count": 0,
            }
            if "language" in h:
                entry["language"] = h["language"]
            by_source[source] = entry

        entry["score"] = float(entry["score"]) + float(h.get("score", 0.0))
        entry["hit_count"] = int(entry["hit_count"]) + 1
        if float(h.get("score", 0.0)) > float(entry["top_score"]):
            entry["top_score"] = float(h["score"])
            if "lines" in h:
                entry["lines"] = h["lines"]
            text = h.get("text") or ""
            entry["preview"] = text[:240]

    ordered = sorted(
        by_source.values(),
        key=lambda e: (e["score"], e["top_score"]),
        reverse=True,
    )
    return ordered[:top_k]


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


def infer_branch_collections(
    collections: list[str],
    *,
    branch_collection_position: str,
) -> dict[str, Any]:
    """Group collection names by inferred branch token.

    Branch inference is heuristic because original branch names are sanitized
    for collection naming and may not be perfectly reversible.
    """
    grouped: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    base_candidates: set[str] = set()
    for name in collections:
        parts = name.split("-")
        if len(parts) >= 2:
            if branch_collection_position == "prefix":
                base_candidates.add(parts[-1])
            else:
                base_candidates.add(parts[0])

    for name in collections:
        branch_token: str | None = None
        if branch_collection_position == "prefix":
            matched_base = None
            for candidate in sorted(base_candidates, key=len, reverse=True):
                suffix = f"-{candidate}"
                if name.endswith(suffix):
                    matched_base = candidate
                    break
            if matched_base is not None:
                branch_token = name[: -(len(matched_base) + 1)]
        else:
            matched_base = None
            for candidate in sorted(base_candidates, key=len, reverse=True):
                prefix = f"{candidate}-"
                if name.startswith(prefix):
                    matched_base = candidate
                    break
            if matched_base is not None:
                branch_token = name[len(matched_base) + 1 :]

        if not branch_token or branch_token in {"project"}:
            unknown.append(name)
            continue

        entry = grouped.setdefault(
            branch_token,
            {
                "branch_name": branch_token,
                "sanitized_branch": branch_token,
                "collections": [],
                "parse_confidence": "heuristic",
                "source": "collection_name",
            },
        )
        entry["collections"].append(name)

    branches = sorted(grouped.values(), key=lambda item: item["branch_name"])
    for item in branches:
        item["collections"].sort()
    unknown.sort()
    return {"branches": branches, "unknown_collections": unknown}


async def list_sources(collection: str | None = None) -> list[dict[str, Any]]:
    """Return source manifests for curation dashboards."""
    collection_name = _resolve_collection(collection)
    store = await get_store()
    grouped = await store.scan_sources(collection_name)
    rows: list[dict[str, Any]] = []
    for source, variants in grouped.items():
        chunk_count = sum(int(v.get("chunk_count", 0) or 0) for v in variants)
        ids: list[str] = []
        for variant in variants:
            ids.extend(str(point_id) for point_id in variant.get("ids", []))
        rows.append(
            {
                "source": source,
                "chunk_count": chunk_count,
                "variant_count": len(variants),
                "ids": ids,
            }
        )
    rows.sort(key=lambda item: item["source"])
    return rows


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


async def mark_useful(
    id: str,
    useful: bool = True,
    collection: str | None = None,
) -> dict[str, Any]:
    """Record a thumbs-up/down on a recall hit by point id.

    Writes a ``feedback`` integer payload field (net votes). Future ``recall``
    calls apply a small score boost proportional to net feedback, capped at
    +/- 50 percent.
    """
    if not id:
        return {"id": id, "feedback": 0, "error": "id required"}
    collection_name = _resolve_collection(collection)
    store = await get_store()
    delta = 1 if useful else -1
    try:
        new_value = await store.bump_feedback(collection_name, id, delta)
    except Exception as exc:  # noqa: BLE001
        return {"id": id, "error": f"{type(exc).__name__}: {exc}"}
    return {"id": id, "collection": collection_name, "feedback": new_value}
