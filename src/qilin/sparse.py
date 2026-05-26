"""Lazy wrapper around FastEmbed's sparse text embedder (BM25)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SparseVector:
    """Sparse vector in the format Qdrant expects.

    ``indices`` are token IDs, ``values`` their corresponding term-frequency
    weights. IDF is applied server-side by Qdrant when the collection's
    sparse-vector config sets ``modifier=IDF``.
    """

    indices: list[int]
    values: list[float]


class SparseEmbedder:
    """Wraps ``fastembed.SparseTextEmbedding`` with a lazy ONNX load.

    The model only materializes on first use; if FastEmbed isn't installed or
    the model fails to load (e.g. no network on first run), every call returns
    ``None`` and the caller is expected to fall back to dense-only retrieval.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._model: object | None = None
        self._unavailable = False

    def _load(self) -> object | None:
        if self._model is not None or self._unavailable:
            return self._model
        try:
            from fastembed import SparseTextEmbedding
        except ImportError as exc:
            logger.warning("fastembed not installed; sparse search disabled (%s)", exc)
            self._unavailable = True
            return None
        try:
            self._model = SparseTextEmbedding(model_name=self._settings.sparse_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load sparse model %r; sparse search disabled (%s)",
                self._settings.sparse_model,
                exc,
            )
            self._unavailable = True
            return None
        return self._model

    @property
    def available(self) -> bool:
        return self._load() is not None

    def embed(self, texts: list[str]) -> list[SparseVector] | None:
        """Encode ``texts`` into BM25 sparse vectors, or return None on failure."""
        if not texts:
            return []
        model = self._load()
        if model is None:
            return None
        try:
            raw = list(model.embed(texts))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sparse embedding failed: %s", exc)
            return None
        out: list[SparseVector] = []
        for vec in raw:
            indices = [int(i) for i in getattr(vec, "indices", [])]
            values = [float(v) for v in getattr(vec, "values", [])]
            out.append(SparseVector(indices=indices, values=values))
        return out

    def embed_one(self, text: str) -> SparseVector | None:
        vectors = self.embed([text])
        if not vectors:
            return None
        return vectors[0]


_sparse_lock = asyncio.Lock()
_sparse: SparseEmbedder | None = None


async def get_sparse_embedder() -> SparseEmbedder:
    """Return a process-wide shared :class:`SparseEmbedder`."""
    global _sparse
    async with _sparse_lock:
        if _sparse is None:
            _sparse = SparseEmbedder()
        return _sparse


async def shutdown_sparse() -> None:
    global _sparse
    async with _sparse_lock:
        _sparse = None
