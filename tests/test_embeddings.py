"""Tests for :mod:`qilin.embeddings` using `respx` to mock Ollama."""

from __future__ import annotations

import httpx
import pytest
import respx

from qilin.config import Settings
from qilin.embeddings import EmbeddingError, EmbedTask, OllamaEmbedder

_OLLAMA = "http://ollama.test:11434"


def _make_settings(**overrides) -> Settings:
    base = dict(
        ollama_base_url=_OLLAMA,
        embedding_dim=4,
        embed_batch_size=2,
    )
    base.update(overrides)
    return Settings(**base)


def _embeddings_payload(n: int, dim: int) -> dict:
    return {"embeddings": [[0.1 * (i + 1)] * dim for i in range(n)]}


@pytest.mark.asyncio
async def test_apply_prefix_uses_task_value() -> None:
    out = OllamaEmbedder._apply_prefix(["a", "b"], EmbedTask.DOCUMENT)
    assert out == ["search_document: a", "search_document: b"]

    out_q = OllamaEmbedder._apply_prefix(["q"], EmbedTask.QUERY)
    assert out_q == ["search_query: q"]


@pytest.mark.asyncio
async def test_apply_prefix_handles_none_text() -> None:
    out = OllamaEmbedder._apply_prefix([None, "x"], EmbedTask.QUERY)  # type: ignore[list-item]
    assert out == ["search_query: ", "search_query: x"]


@pytest.mark.asyncio
async def test_embed_empty_returns_empty() -> None:
    embedder = OllamaEmbedder(_make_settings())
    try:
        assert await embedder.embed([], EmbedTask.DOCUMENT) == []
    finally:
        await embedder.aclose()


@pytest.mark.asyncio
async def test_embed_batches_inputs_and_preserves_order() -> None:
    settings = _make_settings(embed_batch_size=2)
    embedder = OllamaEmbedder(settings)

    with respx.mock(base_url=_OLLAMA, assert_all_called=True) as router:
        route = router.post("/api/embed").mock(
            side_effect=[
                httpx.Response(200, json=_embeddings_payload(2, 4)),
                httpx.Response(200, json=_embeddings_payload(2, 4)),
                httpx.Response(200, json=_embeddings_payload(1, 4)),
            ]
        )

        try:
            vectors = await embedder.embed(
                ["a", "b", "c", "d", "e"], EmbedTask.DOCUMENT
            )
        finally:
            await embedder.aclose()

    assert route.call_count == 3
    assert len(vectors) == 5
    for v in vectors:
        assert len(v) == 4

    sent_bodies = [call.request.read().decode() for call in route.calls]
    assert all("search_document:" in b for b in sent_bodies)


@pytest.mark.asyncio
async def test_embed_query_prefix_used_on_query_task() -> None:
    embedder = OllamaEmbedder(_make_settings(embed_batch_size=4))

    with respx.mock(base_url=_OLLAMA) as router:
        route = router.post("/api/embed").mock(
            return_value=httpx.Response(200, json=_embeddings_payload(1, 4))
        )

        try:
            await embedder.embed(["how does TLS work"], EmbedTask.QUERY)
        finally:
            await embedder.aclose()

    body = route.calls[0].request.read().decode()
    assert "search_query:" in body


@pytest.mark.asyncio
async def test_embed_raises_on_http_error_status() -> None:
    embedder = OllamaEmbedder(_make_settings())

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(
            return_value=httpx.Response(500, text="boom")
        )

        try:
            with pytest.raises(EmbeddingError, match="HTTP 500"):
                await embedder.embed(["x"], EmbedTask.QUERY)
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_embed_raises_on_transport_error() -> None:
    embedder = OllamaEmbedder(_make_settings())

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(side_effect=httpx.ConnectError("no route"))

        try:
            with pytest.raises(EmbeddingError, match="Failed to reach Ollama"):
                await embedder.embed(["x"], EmbedTask.QUERY)
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_embed_raises_on_unexpected_payload_shape() -> None:
    embedder = OllamaEmbedder(_make_settings())

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": "nope"})
        )

        try:
            with pytest.raises(EmbeddingError, match="Unexpected Ollama response"):
                await embedder.embed(["x"], EmbedTask.QUERY)
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_embed_raises_on_dim_mismatch() -> None:
    embedder = OllamaEmbedder(_make_settings(embedding_dim=4))

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(
            return_value=httpx.Response(
                200,
                json={"embeddings": [[0.1, 0.2, 0.3]]},
            )
        )

        try:
            with pytest.raises(EmbeddingError, match="dimension mismatch"):
                await embedder.embed(["x"], EmbedTask.QUERY)
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_health_returns_true_on_success() -> None:
    embedder = OllamaEmbedder(_make_settings())

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(
            return_value=httpx.Response(200, json=_embeddings_payload(1, 4))
        )

        try:
            assert await embedder.health() is True
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_on_error() -> None:
    embedder = OllamaEmbedder(_make_settings())

    with respx.mock(base_url=_OLLAMA) as router:
        router.post("/api/embed").mock(side_effect=httpx.ConnectError("nope"))

        try:
            assert await embedder.health() is False
        finally:
            await embedder.aclose()


@pytest.mark.asyncio
async def test_async_context_manager() -> None:
    settings = _make_settings()
    async with OllamaEmbedder(settings) as embedder:
        assert isinstance(embedder, OllamaEmbedder)


@pytest.mark.asyncio
async def test_get_embedder_caches_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    from qilin import embeddings as emb_module

    monkeypatch.setattr(emb_module, "_embedder", None)

    a = await emb_module.get_embedder()
    b = await emb_module.get_embedder()

    assert a is b
    await emb_module.shutdown_embedder()
    assert emb_module._embedder is None
