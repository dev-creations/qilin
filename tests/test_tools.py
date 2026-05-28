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
    async def test_returns_flat_hits_with_first_class_fields(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.search.return_value = [
            SearchHit(
                id="pid-1",
                score=0.95,
                text="hello",
                payload={
                    "text": "hello",
                    "source": "a.md",
                    "language": "markdown",
                    "start_line": 30,
                    "end_line": 95,
                    "chunk_ordinal": 0,
                    "chunk_count": 1,
                    "document_hash": "abc",
                    "created_at": "2026-01-01T00:00:00Z",
                    "lang": "en",
                    "author": "alice",
                },
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
        assert h["source"] == "a.md"
        assert h["language"] == "markdown"
        assert h["start_line"] == 30
        assert h["end_line"] == 95
        assert h["lines"] == "30-95"
        assert h["chunk_ordinal"] == 0
        assert h["chunk_count"] == 1
        assert h["document_hash"] == "abc"
        assert h["created_at"] == "2026-01-01T00:00:00Z"
        assert h["extra_metadata"] == {"lang": "en", "author": "alice"}
        assert "metadata" not in h
        assert "text" not in h.get("extra_metadata", {})

        fake_embedder.embed.assert_awaited_once()
        fake_store.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_line_hit_renders_unranged_lines(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1]]
        fake_store.search.return_value = [
            SearchHit(
                id="x",
                score=0.5,
                text="t",
                payload={"text": "t", "source": "f", "start_line": 7, "end_line": 7},
            ),
        ]

        hits = await tools.recall(query="q")

        assert hits[0]["lines"] == "7"

    @pytest.mark.asyncio
    async def test_hit_without_line_spans_has_no_lines_field(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1]]
        fake_store.search.return_value = [
            SearchHit(
                id="x",
                score=0.5,
                text="t",
                payload={"text": "t", "source": "f"},
            ),
        ]

        hits = await tools.recall(query="q")

        assert "lines" not in hits[0]
        assert "start_line" not in hits[0]

    @pytest.mark.asyncio
    async def test_hit_with_no_extra_payload_omits_extra_metadata(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1]]
        fake_store.search.return_value = [
            SearchHit(
                id="x",
                score=0.5,
                text="t",
                payload={"text": "t", "source": "f"},
            ),
        ]

        hits = await tools.recall(query="q")

        assert "extra_metadata" not in hits[0]


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


class TestMarkUseful:
    @pytest.mark.asyncio
    async def test_empty_id_returns_error(self, fake_store) -> None:
        out = await tools.mark_useful(id="")
        assert out["error"] == "id required"
        fake_store.bump_feedback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upvote_delegates_with_positive_delta(self, fake_store) -> None:
        fake_store.bump_feedback.return_value = 3
        out = await tools.mark_useful(id="abc", useful=True)
        assert out == {"id": "abc", "collection": "memory", "feedback": 3}
        fake_store.bump_feedback.assert_awaited_once_with("memory", "abc", 1)

    @pytest.mark.asyncio
    async def test_downvote_uses_negative_delta(self, fake_store) -> None:
        fake_store.bump_feedback.return_value = -1
        out = await tools.mark_useful(id="abc", useful=False, collection="scratch")
        assert out == {"id": "abc", "collection": "scratch", "feedback": -1}
        fake_store.bump_feedback.assert_awaited_once_with("scratch", "abc", -1)

    @pytest.mark.asyncio
    async def test_swallows_store_error(self, fake_store) -> None:
        fake_store.bump_feedback.side_effect = RuntimeError("kaboom")
        out = await tools.mark_useful(id="abc")
        assert "error" in out
        assert "RuntimeError" in out["error"]


class TestRememberTTL:
    @pytest.mark.asyncio
    async def test_writes_expires_at_when_collection_has_ttl(
        self, fake_embedder, fake_store, mocker
    ) -> None:
        from qilin.config import CollectionOverride, get_settings

        settings = get_settings()
        mocker.patch.dict(
            settings.collections,
            {"scratch": CollectionOverride(ttl_seconds=60)},
            clear=False,
        )

        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.upsert_chunks.return_value = 1

        await tools.remember(text="ephemeral", collection="scratch")

        args = fake_store.upsert_chunks.await_args.args
        payloads = args[2]
        assert payloads, "expected at least one payload"
        first = payloads[0]
        assert "expires_at" in first, f"expires_at missing from {first!r}"
        assert isinstance(first["expires_at"], str)

    @pytest.mark.asyncio
    async def test_no_expires_at_when_no_ttl(
        self, fake_embedder, fake_store
    ) -> None:
        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.upsert_chunks.return_value = 1

        await tools.remember(text="forever", collection="memory")

        args = fake_store.upsert_chunks.await_args.args
        payloads = args[2]
        assert payloads
        assert "expires_at" not in payloads[0]


class TestRecallFeedbackBoost:
    @pytest.mark.asyncio
    async def test_recall_applies_feedback_boost(
        self, fake_embedder, fake_store, mocker
    ) -> None:
        from qilin.config import get_settings

        settings = get_settings()
        mocker.patch.object(settings, "recall_log_path", "")

        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.search.return_value = [
            SearchHit(
                id="up",
                score=0.5,
                text="upvoted",
                payload={"text": "upvoted", "source": "a", "feedback": 4},
            ),
            SearchHit(
                id="neutral",
                score=0.55,
                text="neutral",
                payload={"text": "neutral", "source": "b"},
            ),
        ]

        out = await tools.recall(query="q", top_k=2)

        assert out[0]["id"] == "up"
        assert out[0]["score"] > 0.5
        assert out[0]["score"] > out[1]["score"]


class TestWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_recall_filters_hits_by_workspace_roots(
        self, fake_embedder, fake_store, mocker
    ) -> None:
        from qilin.config import get_settings

        settings = get_settings()
        mocker.patch.object(settings, "recall_log_path", "")
        mocker.patch.object(settings, "workspace_scoping_enabled", True)
        mocker.patch.object(settings, "workspace_scoping_mode", "prefix_filter")

        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.search.return_value = [
            SearchHit(
                id="in",
                score=0.9,
                text="in scope",
                payload={"text": "in scope", "source": "/repo/a.py"},
            ),
            SearchHit(
                id="out",
                score=0.95,
                text="out scope",
                payload={"text": "out scope", "source": "/other/b.py"},
            ),
        ]

        out = await tools.recall(
            query="q",
            top_k=2,
            workspace_roots=["/repo"],
        )
        assert [h["id"] for h in out] == ["in"]

    @pytest.mark.asyncio
    async def test_recall_uses_project_collection_in_hybrid_when_enabled(
        self, fake_embedder, fake_store, mocker
    ) -> None:
        from qilin.config import get_settings

        settings = get_settings()
        mocker.patch.object(settings, "recall_log_path", "")
        mocker.patch.object(settings, "workspace_scoping_enabled", True)
        mocker.patch.object(settings, "workspace_scoping_mode", "hybrid")
        mocker.patch.object(settings, "workspace_use_project_collection", True)

        fake_embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        fake_store.search.return_value = []

        await tools.recall(query="q", workspace_roots=["/repo"])

        called_collection = fake_store.search.await_args.kwargs["collection"]
        assert called_collection.startswith("memory-project-")
