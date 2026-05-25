"""Async Ollama embedding client for `nomic-embed-text-v2-moe`.

The Nomic v2 family expects task-instruction prefixes on inputs:
    - ``search_document: <text>`` for content being stored
    - ``search_query: <text>`` for queries at recall time

We prepend these here so callers never have to think about it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from enum import StrEnum

import httpx

from .config import Settings, get_settings


class EmbedTask(StrEnum):
    """Task-instruction prefix that the Nomic v2 model expects on each input."""

    DOCUMENT = "search_document"
    QUERY = "search_query"


class EmbeddingError(RuntimeError):
    """Raised when the embedding backend fails or returns an unexpected payload."""


class OllamaEmbedder:
    """Thin async client around Ollama's ``/api/embed`` endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            base_url=self._settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OllamaEmbedder:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    @staticmethod
    def _apply_prefix(texts: Iterable[str], task: EmbedTask) -> list[str]:
        prefix = f"{task.value}: "
        return [prefix + (t or "") for t in texts]

    async def embed(
        self,
        texts: list[str],
        task: EmbedTask,
    ) -> list[list[float]]:
        """Embed ``texts`` in batches; preserves input order."""
        if not texts:
            return []

        prefixed = self._apply_prefix(texts, task)
        batch_size = self._settings.embed_batch_size
        results: list[list[float]] = []

        for start in range(0, len(prefixed), batch_size):
            batch = prefixed[start : start + batch_size]
            results.extend(await self._embed_batch(batch))

        return results

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        payload = {"model": self._settings.embedding_model, "input": batch}
        try:
            response = await self._client.post("/api/embed", json=payload)
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"Failed to reach Ollama at {self._settings.ollama_base_url}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise EmbeddingError(
                f"Ollama /api/embed returned HTTP {response.status_code}: {response.text}"
            )

        data = response.json()
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise EmbeddingError(
                f"Unexpected Ollama response shape: got {len(vectors) if isinstance(vectors, list) else type(vectors).__name__} "
                f"vectors for {len(batch)} inputs"
            )

        for vec in vectors:
            if not isinstance(vec, list) or len(vec) != self._settings.embedding_dim:
                raise EmbeddingError(
                    f"Embedding dimension mismatch: expected {self._settings.embedding_dim}, "
                    f"got {len(vec) if isinstance(vec, list) else type(vec).__name__}"
                )
        return vectors

    async def health(self) -> bool:
        """Return True iff a tiny embed roundtrip succeeds."""
        try:
            await self._embed_batch([f"{EmbedTask.QUERY.value}: ping"])
            return True
        except EmbeddingError:
            return False


_embedder_lock = asyncio.Lock()
_embedder: OllamaEmbedder | None = None


async def get_embedder() -> OllamaEmbedder:
    """Return a process-wide shared OllamaEmbedder."""
    global _embedder
    async with _embedder_lock:
        if _embedder is None:
            _embedder = OllamaEmbedder()
        return _embedder


async def shutdown_embedder() -> None:
    global _embedder
    async with _embedder_lock:
        if _embedder is not None:
            await _embedder.aclose()
            _embedder = None
