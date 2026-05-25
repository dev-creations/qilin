"""Tests for :mod:`qilin.tools` with mocked embedder and store."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qilin import tools
from qilin.embeddings import EmbedTask
from qilin.store import SearchHit


@pytest.fixture
def fake_embedder(mocker):
    """Replace `tools.get_embedder` with an AsyncMock factory."""
    embedder = AsyncMock()
    mocker.patch("qilin.tools.get_embedder", AsyncMock(return_value=embedder))
    return embedder


@pytest.fixture
def fake_store(mocker):
    """Replace `tools.get_store` with an AsyncMock factory."""
    store = AsyncMock()
    mocker.patch("qilin.tools.get_store", AsyncMock(return_value=store))
    return store


class TestRemember:
    @pytest.mark.asyncio
    async def test_empty_text_short_circuits(self, fake_embedder, fake_store) -> None:
        result = await tools.remember(text="")
        assert result["chunks_written"] == 0
        assert result["ids"] == []
        fake_embedder.embed.assert_not_awaited()
        fake_store.upsert_chunks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_whitespace_only_short_circuits(self, fake_embedder, fake_store) -> None:
        result = await tools.remember(text="   \n\n   \t")
        assert result["chunks_written"] == 0
        fake_embedder.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_happy_path_writes_chunks(self, fake_embedder, fake_store) -> None:
        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.upsert_chunks.return_value = 1

        result = await tools.remember(
            text="Hello world. This is a short sentence.",
            collection="custom",
            metadata={"author": "alice"},
            source="notes.md",
        )

        assert result["collection"] == "custom"
        assert result["chunks_written"] == 1
        assert len(result["ids"]) == 1
        assert "document_hash" in result

        fake_embedder.embed.assert_awaited_once()
        args, kwargs = fake_embedder.embed.await_args
        assert kwargs.get("task", args[1] if len(args) > 1 else None) == EmbedTask.DOCUMENT

        fake_store.upsert_chunks.assert_awaited_once()
        upsert_kwargs = fake_store.upsert_chunks.await_args
        all_args = upsert_kwargs.args
        coll = all_args[0] if all_args else upsert_kwargs.kwargs["collection"]
        assert coll == "custom"

    @pytest.mark.asyncio
    async def test_default_collection_used_when_none(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.upsert_chunks.return_value = 1

        result = await tools.remember(text="Short text fits in one chunk.")

        assert result["collection"] == "memory"


class TestRecall:
    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, fake_embedder, fake_store) -> None:
        assert await tools.recall(query="") == []
        assert await tools.recall(query="   ") == []
        fake_embedder.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zero_top_k_returns_empty(self, fake_embedder, fake_store) -> None:
        assert await tools.recall(query="hi", top_k=0) == []
        fake_embedder.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_vectors_returns_empty(self, fake_embedder, fake_store) -> None:
        fake_embedder.embed.return_value = []

        assert await tools.recall(query="hi") == []
        fake_store.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_mapped_hits_with_metadata_stripped(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.search.return_value = [
            SearchHit(
                id="pid-1",
                score=0.95,
                text="hello",
                payload={"text": "hello", "source": "a.md", "lang": "en"},
            ),
        ]

        hits = await tools.recall(
            query="hi",
            collection="docs",
            top_k=3,
            filter={"lang": "en"},
            score_threshold=0.5,
        )

        assert len(hits) == 1
        h = hits[0]
        assert h["id"] == "pid-1"
        assert h["score"] == pytest.approx(0.95)
        assert h["text"] == "hello"
        assert "text" not in h["metadata"]
        assert h["metadata"]["source"] == "a.md"

        fake_embedder.embed.assert_awaited_once()
        fake_store.search.assert_awaited_once()


class TestForget:
    @pytest.mark.asyncio
    async def test_requires_ids_or_filter(self, fake_store) -> None:
        out = await tools.forget()
        assert out["deleted"] == 0
        assert "error" in out
        fake_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delegates_to_store_with_ids(self, fake_store) -> None:
        fake_store.delete.return_value = 3
        out = await tools.forget(ids=["a", "b", "c"])
        assert out["deleted"] == 3

    @pytest.mark.asyncio
    async def test_delegates_to_store_with_filter(self, fake_store) -> None:
        fake_store.delete.return_value = 7
        out = await tools.forget(filter={"lang": "py"}, collection="docs")
        assert out["deleted"] == 7
        assert out["collection"] == "docs"


class TestCollections:
    @pytest.mark.asyncio
    async def test_list_collections(self, fake_store) -> None:
        fake_store.list_collections.return_value = ["a", "b"]
        assert await tools.list_collections() == ["a", "b"]

    @pytest.mark.asyncio
    async def test_create_collection_empty_name_returns_error(self, fake_store) -> None:
        out = await tools.create_collection("")
        assert out["created"] is False
        assert "error" in out
        fake_store.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_collection_whitespace_name_returns_error(
        self, fake_store
    ) -> None:
        out = await tools.create_collection("   ")
        assert out["created"] is False
        fake_store.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_collection_happy_path(self, fake_store) -> None:
        fake_store.create_collection.return_value = True
        out = await tools.create_collection("memes")
        assert out == {"name": "memes", "created": True}


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_returns_store_dict(self, fake_store) -> None:
        fake_store.stats.return_value = {"collection": "memory", "exists": True}

        out = await tools.stats()

        assert out["collection"] == "memory"
        fake_store.stats.assert_awaited_once_with("memory")


def test_resolve_collection_uses_default() -> None:
    assert tools._resolve_collection(None) == "memory"
    assert tools._resolve_collection("custom") == "custom"


def test_search_hit_helper_smoke() -> None:
    hit = SearchHit(id="x", score=1.0, text="t", payload={"text": "t"})
    payload = SimpleNamespace(**hit.payload)
    assert payload.text == "t"
