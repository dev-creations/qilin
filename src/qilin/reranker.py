"""Lazy wrapper around FastEmbed's cross-encoder reranker."""

from __future__ import annotations

import asyncio
import logging

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class Reranker:
    """Score (query, document) pairs with a cross-encoder.

    The model downloads on first use (~80 MB); we keep the load lazy so an
    install with ``rerank_enabled=False`` never pays that cost. If FastEmbed
    isn't installed or the model fails to load we degrade gracefully: every
    call returns ``None`` and the caller is expected to fall back to vector
    ordering.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._model: object | None = None
        self._unavailable = False

    def _load(self) -> object | None:
        if self._model is not None or self._unavailable:
            return self._model
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as exc:
            logger.warning("fastembed cross-encoder unavailable: %s", exc)
            self._unavailable = True
            return None
        try:
            self._model = TextCrossEncoder(model_name=self._settings.rerank_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load rerank model %r: %s",
                self._settings.rerank_model,
                exc,
            )
            self._unavailable = True
            return None
        return self._model

    @property
    def available(self) -> bool:
        return self._load() is not None

    def score(self, query: str, documents: list[str]) -> list[float] | None:
        """Return per-document scores or None when the model is unavailable."""
        if not documents:
            return []
        model = self._load()
        if model is None:
            return None
        try:
            raw = list(model.rerank(query, documents))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rerank scoring failed: %s", exc)
            return None
        return [float(s) for s in raw]


_rerank_lock = asyncio.Lock()
_reranker: Reranker | None = None


async def get_reranker() -> Reranker:
    """Return a process-wide shared :class:`Reranker`."""
    global _reranker
    async with _rerank_lock:
        if _reranker is None:
            _reranker = Reranker()
        return _reranker


async def shutdown_reranker() -> None:
    global _reranker
    async with _rerank_lock:
        _reranker = None
