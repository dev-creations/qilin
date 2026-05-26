"""Tests for the recall post-processing knobs: MMR, group-by-source, neighbor expansion."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from qilin import tools
from qilin.store import SearchHit


@pytest.fixture
def fake_embedder(mocker):
    embedder = AsyncMock()
    embedder.embed.return_value = [[1.0, 0.0, 0.0]]
    mocker.patch("qilin.tools.get_embedder", AsyncMock(return_value=embedder))
    return embedder


@pytest.fixture
def fake_store(mocker):
    store = AsyncMock()
    mocker.patch("qilin.tools.get_store", AsyncMock(return_value=store))
    return store


def _hit(
    pid: str,
    score: float,
    *,
    source: str = "src.md",
    ordinal: int = 0,
    text: str = "x",
    vector: list[float] | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> SearchHit:
    payload: dict = {"text": text, "source": source, "chunk_ordinal": ordinal}
    if start_line is not None:
        payload["start_line"] = start_line
    if end_line is not None:
        payload["end_line"] = end_line
    return SearchHit(id=pid, score=score, text=text, payload=payload, vector=vector)


class TestCosine:
    def test_cosine_identical_vectors_is_one(self) -> None:
        assert tools._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_cosine_orthogonal_is_zero(self) -> None:
        assert tools._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_cosine_zero_magnitude_safe(self) -> None:
        assert tools._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert tools._cosine([], [1.0]) == 0.0


class TestMMR:
    def test_mmr_with_no_vectors_falls_back(self) -> None:
        hits = [_hit("a", 0.9), _hit("b", 0.8)]
        out = tools._mmr_rerank([1.0, 0.0], hits, 0.5, top_k=2)
        assert [h.id for h in out] == ["a", "b"]

    def test_mmr_prefers_diverse_after_first_pick(self) -> None:
        # With lambda=0.3 (diversity-favoring), once we pick `a` (which is
        # identical to the query), the near-duplicate `b` is penalized hard
        # by the diversity term and the orthogonal `c` wins.
        query = [1.0, 0.0]
        near_query_a = _hit("a", 0.95, vector=[1.0, 0.0])
        near_query_b = _hit("b", 0.94, vector=[0.95, 0.1])
        diverse = _hit("c", 0.10, vector=[0.0, 1.0])

        out = tools._mmr_rerank(query, [near_query_a, near_query_b, diverse], 0.3, top_k=2)
        assert out[0].id == "a"
        assert out[1].id == "c"

    def test_mmr_lambda_one_reproduces_similarity_order(self) -> None:
        # cos to query=[1,0]: b=1.0, a=0.707, c=0.287
        query = [1.0, 0.0]
        hits = [
            _hit("a", 0.7, vector=[0.7, 0.7]),
            _hit("b", 0.9, vector=[1.0, 0.0]),
            _hit("c", 0.5, vector=[0.3, 1.0]),
        ]

        out = tools._mmr_rerank(query, hits, 1.0, top_k=3)
        assert [h.id for h in out] == ["b", "a", "c"]

    def test_mmr_empty_hits_returns_empty(self) -> None:
        assert tools._mmr_rerank([1.0], [], 0.5, top_k=5) == []

    def test_mmr_top_k_zero_returns_empty(self) -> None:
        assert tools._mmr_rerank([1.0], [_hit("a", 0.9, vector=[1.0])], 0.5, top_k=0) == []

    def test_mmr_clamps_lambda_out_of_range(self) -> None:
        hits = [_hit("a", 0.9, vector=[1.0])]
        out = tools._mmr_rerank([1.0], hits, 2.0, top_k=1)
        assert out[0].id == "a"


class TestGroupBySource:
    def test_keeps_highest_score_per_source(self) -> None:
        hits = [
            _hit("a", 0.9, source="x.md"),
            _hit("b", 0.8, source="x.md"),
            _hit("c", 0.7, source="y.md"),
        ]
        out = tools._group_by_source(hits)
        ids = {h.id for h in out}
        assert ids == {"a", "c"}

    def test_unsourced_hits_pass_through(self) -> None:
        h1 = SearchHit(id="a", score=0.9, text="", payload={"text": ""})
        h2 = SearchHit(id="b", score=0.8, text="", payload={"text": ""})
        out = tools._group_by_source([h1, h2])
        assert {h.id for h in out} == {"a", "b"}


class TestMergeSiblings:
    def test_merges_text_and_lines(self) -> None:
        siblings = [
            {"text": "first", "start_line": 1, "end_line": 5, "chunk_ordinal": 0},
            {"text": "second", "start_line": 6, "end_line": 10, "chunk_ordinal": 1},
        ]
        anchor = {"id": "x", "score": 0.9, "text": "first", "source": "f.py", "chunk_ordinal": 0}

        merged = tools._merge_siblings(siblings, anchor)

        assert merged["text"] == "first\n\nsecond"
        assert merged["start_line"] == 1
        assert merged["end_line"] == 10
        assert merged["lines"] == "1-10"
        assert merged["chunk_ordinals"] == [0, 1]
        assert merged["score"] == 0.9
        assert merged["id"] == "x"

    def test_no_siblings_returns_anchor(self) -> None:
        anchor = {"id": "x", "text": "t"}
        assert tools._merge_siblings([], anchor) is anchor


class TestRecallWithKnobs:
    @pytest.mark.asyncio
    async def test_mmr_requests_vectors(self, fake_embedder, fake_store) -> None:
        fake_store.search.return_value = [
            _hit("a", 0.9, vector=[1.0, 0.0]),
            _hit("b", 0.8, vector=[0.0, 1.0]),
        ]
        await tools.recall(query="q", mmr_lambda=0.5)
        kwargs = fake_store.search.await_args.kwargs
        assert kwargs["with_vectors"] is True

    @pytest.mark.asyncio
    async def test_default_recall_does_not_request_vectors(
        self, fake_embedder, fake_store
    ) -> None:
        fake_store.search.return_value = []
        await tools.recall(query="q")
        kwargs = fake_store.search.await_args.kwargs
        assert kwargs["with_vectors"] is False

    @pytest.mark.asyncio
    async def test_group_by_source_dedupes_results(self, fake_embedder, fake_store) -> None:
        fake_store.search.return_value = [
            _hit("a", 0.9, source="x.md"),
            _hit("b", 0.8, source="x.md"),
            _hit("c", 0.7, source="y.md"),
        ]

        hits = await tools.recall(query="q", group_by_source=True, top_k=5)
        sources = {h["source"] for h in hits}
        assert sources == {"x.md", "y.md"}
        ids = {h["id"] for h in hits}
        assert ids == {"a", "c"}

    @pytest.mark.asyncio
    async def test_context_window_zero_skips_fetch(self, fake_embedder, fake_store) -> None:
        fake_store.search.return_value = [_hit("a", 0.9, source="f.py", ordinal=2)]

        await tools.recall(query="q", context_window=0)

        fake_store.fetch_neighbors.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_window_expands_hit_with_siblings(
        self, fake_embedder, fake_store
    ) -> None:
        fake_store.search.return_value = [
            _hit("a", 0.9, source="f.py", ordinal=2, text="middle",
                 start_line=10, end_line=15),
        ]
        fake_store.fetch_neighbors.return_value = [
            {"text": "before", "source": "f.py", "chunk_ordinal": 1,
             "start_line": 5, "end_line": 9},
            {"text": "middle", "source": "f.py", "chunk_ordinal": 2,
             "start_line": 10, "end_line": 15},
            {"text": "after", "source": "f.py", "chunk_ordinal": 3,
             "start_line": 16, "end_line": 20},
        ]

        hits = await tools.recall(query="q", context_window=1)

        fake_store.fetch_neighbors.assert_awaited_once()
        await_info = fake_store.fetch_neighbors.await_args
        args = await_info.args
        ords = args[2] if len(args) >= 3 else await_info.kwargs["ordinals"]
        assert sorted(ords) == [1, 2, 3]

        assert len(hits) == 1
        assert hits[0]["text"] == "before\n\nmiddle\n\nafter"
        assert hits[0]["start_line"] == 5
        assert hits[0]["end_line"] == 20
        assert hits[0]["lines"] == "5-20"
        assert hits[0]["chunk_ordinals"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_context_window_dedupes_overlapping_ranges(
        self, fake_embedder, fake_store
    ) -> None:
        fake_store.search.return_value = [
            _hit("a", 0.9, source="f.py", ordinal=5, text="five"),
            _hit("b", 0.8, source="f.py", ordinal=6, text="six"),
        ]

        async def fake_neighbors(_collection, source, ordinals):
            return [
                {"text": f"chunk{o}", "source": source, "chunk_ordinal": o}
                for o in ordinals
            ]

        fake_store.fetch_neighbors.side_effect = fake_neighbors

        hits = await tools.recall(query="q", context_window=2)

        assert len(hits) == 1
        assert fake_store.fetch_neighbors.await_count == 1

    @pytest.mark.asyncio
    async def test_context_window_skips_hits_missing_source(
        self, fake_embedder, fake_store
    ) -> None:
        hit_without_source = SearchHit(
            id="a", score=0.9, text="t", payload={"text": "t"}
        )
        fake_store.search.return_value = [hit_without_source]

        hits = await tools.recall(query="q", context_window=2)

        fake_store.fetch_neighbors.assert_not_called()
        assert len(hits) == 1

    @pytest.mark.asyncio
    async def test_context_window_falls_back_when_no_siblings(
        self, fake_embedder, fake_store
    ) -> None:
        fake_store.search.return_value = [
            _hit("a", 0.9, source="f.py", ordinal=2, text="orig",
                 start_line=10, end_line=12),
        ]
        fake_store.fetch_neighbors.return_value = []

        hits = await tools.recall(query="q", context_window=1)

        assert len(hits) == 1
        assert hits[0]["text"] == "orig"


class TestStoreFetchNeighbors:
    @pytest.mark.asyncio
    async def test_empty_ordinals_short_circuits(self, mocker) -> None:
        from qilin.store import VectorStore

        instance = mocker.AsyncMock()
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=instance)
        store = VectorStore()

        result = await store.fetch_neighbors("c", "src.md", [])
        assert result == []
        instance.scroll.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scroll_filter_and_sort(self, mocker) -> None:
        from types import SimpleNamespace

        from qilin.store import VectorStore

        instance = mocker.AsyncMock()
        instance.scroll.return_value = (
            [
                SimpleNamespace(payload={"chunk_ordinal": 3, "text": "c"}),
                SimpleNamespace(payload={"chunk_ordinal": 1, "text": "a"}),
                SimpleNamespace(payload={"chunk_ordinal": 2, "text": "b"}),
            ],
            None,
        )
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=instance)
        store = VectorStore()

        out = await store.fetch_neighbors("c", "src.md", [1, 2, 3])

        assert [p["chunk_ordinal"] for p in out] == [1, 2, 3]
        instance.scroll.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_on_404(self, mocker) -> None:
        import httpx
        from qdrant_client.http.exceptions import UnexpectedResponse

        from qilin.store import VectorStore

        instance = mocker.AsyncMock()
        instance.scroll.side_effect = UnexpectedResponse(
            status_code=404, reason_phrase="x", content=b"", headers=httpx.Headers()
        )
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=instance)
        store = VectorStore()

        assert await store.fetch_neighbors("c", "src.md", [1, 2]) == []
