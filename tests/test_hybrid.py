"""Tests for sparse/hybrid retrieval and the reranker glue."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client.http import models as qm

from qilin import tools
from qilin.sparse import SparseVector
from qilin.store import SearchHit, VectorStore


@pytest.fixture
def hybrid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYBRID_ENABLED", "true")
    from qilin import config as config_module

    config_module.get_settings.cache_clear()


@pytest.fixture
def mock_async_client(mocker):
    instance = AsyncMock()
    mocker.patch("qilin.store.AsyncQdrantClient", return_value=instance)
    return instance


class TestEnsureCollectionLayout:
    @pytest.mark.asyncio
    async def test_creates_named_vectors_when_hybrid_enabled(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()

        await store.ensure_collection("c")

        kwargs = client.create_collection.await_args.kwargs
        assert isinstance(kwargs["vectors_config"], dict)
        assert "dense" in kwargs["vectors_config"]
        assert "sparse_vectors_config" in kwargs
        assert "bm25" in kwargs["sparse_vectors_config"]

    @pytest.mark.asyncio
    async def test_creates_legacy_single_vector_when_disabled(
        self, mock_async_client
    ) -> None:
        client = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()

        await store.ensure_collection("c")

        kwargs = client.create_collection.await_args.kwargs
        assert isinstance(kwargs["vectors_config"], qm.VectorParams)
        assert "sparse_vectors_config" not in kwargs


class TestUpsertChunksHybrid:
    @pytest.mark.asyncio
    async def test_writes_named_vectors_when_hybrid_enabled(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.collection_exists.return_value = True
        store = VectorStore()

        sparse = [SparseVector(indices=[1, 2], values=[0.5, 0.6])]
        await store.upsert_chunks(
            "c",
            vectors=[[0.1, 0.2]],
            payloads=[{"text": "x"}],
            ids=["id-1"],
            sparse_vectors=sparse,
        )

        kwargs = client.upsert.await_args.kwargs
        point = kwargs["points"][0]
        assert isinstance(point.vector, dict)
        assert "dense" in point.vector
        assert "bm25" in point.vector

    @pytest.mark.asyncio
    async def test_validates_sparse_length(
        self, hybrid_settings, mock_async_client
    ) -> None:
        store = VectorStore()
        with pytest.raises(ValueError):
            await store.upsert_chunks(
                "c",
                vectors=[[0.1]],
                payloads=[{"text": "x"}],
                ids=["1"],
                sparse_vectors=[
                    SparseVector(indices=[], values=[]),
                    SparseVector(indices=[], values=[]),
                ],
            )

    @pytest.mark.asyncio
    async def test_hybrid_mode_without_sparse_falls_to_dense(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.collection_exists.return_value = True
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search("c", [0.1], mode="hybrid", sparse_vector=None)

        kwargs = client.query_points.await_args.kwargs
        assert "prefetch" not in kwargs


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_hybrid_uses_prefetch_and_fusion(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search(
            "c",
            [0.1, 0.2],
            mode="hybrid",
            sparse_vector=SparseVector(indices=[1], values=[1.0]),
        )

        kwargs = client.query_points.await_args.kwargs
        assert "prefetch" in kwargs
        assert isinstance(kwargs["query"], qm.FusionQuery)

    @pytest.mark.asyncio
    async def test_sparse_mode_queries_bm25(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search(
            "c",
            [0.1],
            mode="sparse",
            sparse_vector=SparseVector(indices=[1], values=[1.0]),
        )

        kwargs = client.query_points.await_args.kwargs
        assert kwargs["using"] == "bm25"

    @pytest.mark.asyncio
    async def test_dense_mode_with_hybrid_enabled_uses_named_vector(
        self, hybrid_settings, mock_async_client
    ) -> None:
        client = mock_async_client
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search("c", [0.1], mode="dense")

        kwargs = client.query_points.await_args.kwargs
        assert kwargs["using"] == "dense"


class TestRerankerGlue:
    @pytest.mark.asyncio
    async def test_rerank_reorders_by_cross_encoder(self, mocker) -> None:
        fake_reranker = MagicMock()
        fake_reranker.available = True
        fake_reranker.score.return_value = [0.1, 0.9, 0.5]

        mocker.patch(
            "qilin.tools.get_reranker", AsyncMock(return_value=fake_reranker)
        )

        hits = [
            SearchHit(id="a", score=0.5, text="alpha", payload={"text": "alpha"}),
            SearchHit(id="b", score=0.5, text="bravo", payload={"text": "bravo"}),
            SearchHit(id="c", score=0.5, text="charlie", payload={"text": "charlie"}),
        ]

        reordered = await tools._rerank_hits("query", hits)

        assert [h.id for h in reordered] == ["b", "c", "a"]
        assert reordered[0].score == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_rerank_noop_when_unavailable(self, mocker) -> None:
        fake_reranker = MagicMock()
        fake_reranker.available = False

        mocker.patch(
            "qilin.tools.get_reranker", AsyncMock(return_value=fake_reranker)
        )

        hits = [SearchHit(id="a", score=0.5, text="x", payload={"text": "x"})]
        result = await tools._rerank_hits("q", hits)
        assert result is hits


class TestResolveMode:
    def test_resolve_mode_none_defaults_dense_without_hybrid(self) -> None:
        assert tools._resolve_mode(None) == "dense"

    def test_resolve_mode_none_defaults_hybrid_with_setting(
        self, hybrid_settings
    ) -> None:
        assert tools._resolve_mode(None) == "hybrid"

    def test_resolve_mode_lowercases(self) -> None:
        assert tools._resolve_mode("HYBRID") == "hybrid"

    def test_resolve_mode_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            tools._resolve_mode("rainbow")


class TestRecallWithRerank:
    @pytest.mark.asyncio
    async def test_recall_calls_rerank_when_enabled(self, mocker) -> None:
        embedder = AsyncMock()
        embedder.embed.return_value = [[1.0, 0.0]]
        store = AsyncMock()
        store.search.return_value = [
            SearchHit(id="a", score=0.1, text="a", payload={"text": "a"}),
            SearchHit(id="b", score=0.2, text="b", payload={"text": "b"}),
        ]

        reranker = MagicMock()
        reranker.available = True
        reranker.score.return_value = [0.95, 0.05]

        mocker.patch("qilin.tools.get_embedder", AsyncMock(return_value=embedder))
        mocker.patch("qilin.tools.get_store", AsyncMock(return_value=store))
        mocker.patch("qilin.tools.get_reranker", AsyncMock(return_value=reranker))

        hits = await tools.recall(query="q", top_k=2, rerank=True)

        assert hits[0]["id"] == "a"
        assert hits[1]["id"] == "b"


class TestRecallFiles:
    @pytest.mark.asyncio
    async def test_recall_files_groups_and_sums(self, mocker) -> None:
        mocker.patch(
            "qilin.tools.recall",
            AsyncMock(
                return_value=[
                    {
                        "id": "1",
                        "score": 0.9,
                        "text": "fn alpha() {}",
                        "source": "a.rs",
                        "language": "rust",
                        "lines": "10-15",
                    },
                    {
                        "id": "2",
                        "score": 0.7,
                        "text": "fn alpha_helper() {}",
                        "source": "a.rs",
                        "language": "rust",
                    },
                    {
                        "id": "3",
                        "score": 0.8,
                        "text": "fn beta() {}",
                        "source": "b.rs",
                        "language": "rust",
                        "lines": "20-25",
                    },
                ]
            ),
        )

        out = await tools.recall_files(query="alpha", top_k=5)

        assert [e["source"] for e in out] == ["a.rs", "b.rs"]
        a = out[0]
        assert a["score"] == pytest.approx(1.6)
        assert a["top_score"] == pytest.approx(0.9)
        assert a["hit_count"] == 2
        assert a["preview"].startswith("fn alpha()")
        assert a["lines"] == "10-15"
        assert a["language"] == "rust"

    @pytest.mark.asyncio
    async def test_recall_files_skips_sourceless_hits(self, mocker) -> None:
        mocker.patch(
            "qilin.tools.recall",
            AsyncMock(return_value=[{"id": "x", "score": 0.5, "text": "t"}]),
        )

        out = await tools.recall_files(query="q")
        assert out == []

    @pytest.mark.asyncio
    async def test_recall_files_empty_query(self) -> None:
        assert await tools.recall_files(query="") == []
        assert await tools.recall_files(query="   ") == []

    @pytest.mark.asyncio
    async def test_recall_files_zero_top_k(self) -> None:
        assert await tools.recall_files(query="q", top_k=0) == []


class TestSparseEmbedder:
    def test_embed_returns_none_when_unavailable(self) -> None:
        from qilin.sparse import SparseEmbedder

        emb = SparseEmbedder()
        emb._unavailable = True
        assert emb.embed(["hello"]) is None

    def test_embed_one_returns_none_when_unavailable(self) -> None:
        from qilin.sparse import SparseEmbedder

        emb = SparseEmbedder()
        emb._unavailable = True
        assert emb.embed_one("hello") is None

    def test_embed_empty_list_returns_empty_list(self) -> None:
        from qilin.sparse import SparseEmbedder

        emb = SparseEmbedder()
        assert emb.embed([]) == []

    def test_available_property_returns_false_when_unavailable(self) -> None:
        from qilin.sparse import SparseEmbedder

        emb = SparseEmbedder()
        emb._unavailable = True
        assert emb.available is False

    def test_embed_returns_vectors_with_mocked_model(self) -> None:
        from qilin.sparse import SparseEmbedder

        fake_model = MagicMock()
        fake_model.embed.return_value = iter(
            [SimpleNamespace(indices=[1, 5], values=[0.4, 0.7])]
        )

        emb = SparseEmbedder()
        emb._model = fake_model
        result = emb.embed(["hello"])
        assert result is not None
        assert result[0].indices == [1, 5]
        assert result[0].values == [pytest.approx(0.4), pytest.approx(0.7)]

    def test_embed_handles_model_exception(self) -> None:
        from qilin.sparse import SparseEmbedder

        fake_model = MagicMock()
        fake_model.embed.side_effect = RuntimeError("boom")

        emb = SparseEmbedder()
        emb._model = fake_model
        assert emb.embed(["hello"]) is None

    def test_embed_one_calls_embed(self) -> None:
        from qilin.sparse import SparseEmbedder

        fake_model = MagicMock()
        fake_model.embed.return_value = iter(
            [SimpleNamespace(indices=[2], values=[1.0])]
        )

        emb = SparseEmbedder()
        emb._model = fake_model
        sv = emb.embed_one("hi")
        assert sv is not None
        assert sv.indices == [2]


class TestReranker:
    def test_score_returns_none_when_unavailable(self) -> None:
        from qilin.reranker import Reranker

        r = Reranker()
        r._unavailable = True
        assert r.score("q", ["a"]) is None

    def test_score_empty_documents_returns_empty(self) -> None:
        from qilin.reranker import Reranker

        r = Reranker()
        assert r.score("q", []) == []

    def test_score_with_mocked_model(self) -> None:
        from qilin.reranker import Reranker

        fake_model = MagicMock()
        fake_model.rerank.return_value = iter([0.1, 0.9])

        r = Reranker()
        r._model = fake_model
        out = r.score("q", ["a", "b"])
        assert out == [pytest.approx(0.1), pytest.approx(0.9)]

    def test_score_handles_model_exception(self) -> None:
        from qilin.reranker import Reranker

        fake_model = MagicMock()
        fake_model.rerank.side_effect = RuntimeError("boom")

        r = Reranker()
        r._model = fake_model
        assert r.score("q", ["a"]) is None

    def test_available_property_false_when_unavailable(self) -> None:
        from qilin.reranker import Reranker

        r = Reranker()
        r._unavailable = True
        assert r.available is False


class TestSingletons:
    @pytest.mark.asyncio
    async def test_get_sparse_embedder_is_singleton(self, mocker) -> None:
        from qilin import sparse as sparse_module

        mocker.patch.object(sparse_module, "_sparse", None)
        a = await sparse_module.get_sparse_embedder()
        b = await sparse_module.get_sparse_embedder()
        assert a is b
        await sparse_module.shutdown_sparse()
        assert sparse_module._sparse is None

    @pytest.mark.asyncio
    async def test_get_reranker_is_singleton(self, mocker) -> None:
        from qilin import reranker as reranker_module

        mocker.patch.object(reranker_module, "_reranker", None)
        a = await reranker_module.get_reranker()
        b = await reranker_module.get_reranker()
        assert a is b
        await reranker_module.shutdown_reranker()
        assert reranker_module._reranker is None
