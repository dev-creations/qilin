"""Tests for :mod:`qilin.store` covering pure helpers and the async wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from qdrant_client.http import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from qilin.store import (
    POINT_ID_NAMESPACE,
    VectorStore,
    _build_filter,
    _content_hash,
    build_payload,
    deterministic_point_id,
)


def _make_unexpected_response(status_code: int) -> UnexpectedResponse:
    """Construct an `UnexpectedResponse` with the given status code."""
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase="boom",
        content=b"",
        headers=httpx.Headers(),
    )


@pytest.fixture
def mock_async_client(mocker):
    """Patch `AsyncQdrantClient` so `VectorStore()` uses an AsyncMock client."""
    instance = AsyncMock()
    constructor = mocker.patch("qilin.store.AsyncQdrantClient", return_value=instance)
    return instance, constructor


class TestPureHelpers:
    def test_content_hash_is_stable_and_unique(self) -> None:
        assert _content_hash("hello") == _content_hash("hello")
        assert _content_hash("hello") != _content_hash("world")
        assert len(_content_hash("x")) == 64

    def test_deterministic_point_id_stable_for_same_inputs(self) -> None:
        a = deterministic_point_id("src.txt", "abc", 0)
        b = deterministic_point_id("src.txt", "abc", 0)
        assert a == b

    def test_deterministic_point_id_differs_for_ordinal(self) -> None:
        a = deterministic_point_id("src.txt", "abc", 0)
        b = deterministic_point_id("src.txt", "abc", 1)
        assert a != b

    def test_deterministic_point_id_differs_for_point_kind(self) -> None:
        parent = deterministic_point_id("src.txt", "abc", 0, point_kind="parent")
        child = deterministic_point_id("src.txt", "abc", 0, point_kind="child")
        assert parent != child

    def test_deterministic_point_id_differs_for_parent_ordinal(self) -> None:
        a = deterministic_point_id(
            "src.txt", "abc", 0, point_kind="child", parent_ordinal=1
        )
        b = deterministic_point_id(
            "src.txt", "abc", 0, point_kind="child", parent_ordinal=2
        )
        assert a != b

    def test_deterministic_point_id_differs_for_source(self) -> None:
        a = deterministic_point_id("a.txt", "abc", 0)
        b = deterministic_point_id("b.txt", "abc", 0)
        assert a != b

    def test_deterministic_point_id_accepts_none_source(self) -> None:
        pid = deterministic_point_id(None, "abc", 0)
        assert pid

    def test_point_id_namespace_is_stable(self) -> None:
        assert str(POINT_ID_NAMESPACE) == "3f9a6e1c-2b1a-4b8a-9a4c-7f1d3c2e8f01"


class TestBuildPayload:
    def test_includes_required_fields(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="src.md",
            extra=None,
        )
        assert payload["text"] == "hello"
        assert payload["chunk_ordinal"] == 0
        assert payload["chunk_count"] == 1
        assert payload["document_hash"] == "abc"
        assert payload["source"] == "src.md"
        assert "created_at" in payload

    def test_omits_source_when_none(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source=None,
            extra=None,
        )
        assert "source" not in payload

    def test_extra_does_not_overwrite_reserved_keys(self) -> None:
        payload = build_payload(
            chunk_text="real",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="s",
            extra={"text": "evil", "custom": "ok"},
        )
        assert payload["text"] == "real"
        assert payload["custom"] == "ok"

    def test_extra_none_is_safe(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source=None,
            extra=None,
        )
        assert payload["text"] == "hello"

    def test_includes_line_spans_when_provided(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="src.md",
            extra=None,
            start_line=12,
            end_line=34,
        )
        assert payload["start_line"] == 12
        assert payload["end_line"] == 34

    def test_omits_line_spans_when_missing(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="src.md",
            extra=None,
        )
        assert "start_line" not in payload
        assert "end_line" not in payload

    def test_writes_defines_imports_signature_language(self) -> None:
        payload = build_payload(
            chunk_text="t",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="x.py",
            extra=None,
            defines=("foo", "Bar"),
            imports=["import os"],
            signature="def foo():",
            language="python",
        )
        assert payload["defines"] == ["foo", "Bar"]
        assert payload["imports"] == ["import os"]
        assert payload["signature"] == "def foo():"
        assert payload["language"] == "python"

    def test_omits_optional_code_fields_when_empty(self) -> None:
        payload = build_payload(
            chunk_text="t",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="x.md",
            extra=None,
        )
        assert "defines" not in payload
        assert "imports" not in payload
        assert "signature" not in payload
        assert "language" not in payload

    def test_writes_hierarchy_fields(self) -> None:
        payload = build_payload(
            chunk_text="t",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="x.py",
            extra=None,
            hierarchy={"is_parent": True, "parent_id": "pid"},
        )
        assert payload["is_parent"] is True
        assert payload["parent_id"] == "pid"

    def test_extra_does_not_overwrite_language(self) -> None:
        payload = build_payload(
            chunk_text="t",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="x.py",
            extra={"language": "ignored"},
            language="python",
        )
        assert payload["language"] == "python"

    def test_extra_does_not_overwrite_line_spans(self) -> None:
        payload = build_payload(
            chunk_text="hello",
            chunk_ordinal=0,
            chunk_count=1,
            document_hash="abc",
            source="src.md",
            extra={"start_line": 999, "end_line": 999},
            start_line=1,
            end_line=5,
        )
        assert payload["start_line"] == 1
        assert payload["end_line"] == 5


class TestBuildFilter:
    def test_none_returns_none(self) -> None:
        assert _build_filter(None) is None

    def test_empty_returns_none(self) -> None:
        assert _build_filter({}) is None

    def test_single_value_becomes_match_value(self) -> None:
        flt = _build_filter({"source": "a.md"})
        assert isinstance(flt, qm.Filter)
        assert flt.must is not None
        assert len(flt.must) == 1
        cond = flt.must[0]
        assert cond.key == "source"
        assert isinstance(cond.match, qm.MatchValue)
        assert cond.match.value == "a.md"

    def test_list_value_becomes_match_any(self) -> None:
        flt = _build_filter({"language": ["py", "ts"]})
        assert flt is not None
        cond = flt.must[0]
        assert isinstance(cond.match, qm.MatchAny)
        assert cond.match.any == ["py", "ts"]

    def test_raw_passthrough(self) -> None:
        raw = {"must_not": [{"key": "deprecated", "match": {"value": True}}]}
        flt = _build_filter({"__raw__": raw})
        assert isinstance(flt, qm.Filter)
        assert flt.must_not is not None

    def test_prefix_key_builds_should_match_text(self) -> None:
        flt = _build_filter({"source__prefix": ["/repo/a", "/repo/b"]})
        assert isinstance(flt, qm.Filter)
        assert flt.should is not None
        assert len(flt.should) == 2
        assert isinstance(flt.should[0].match, qm.MatchText)

    def test_prefix_object_builds_should_match_text(self) -> None:
        flt = _build_filter({"__prefix__": {"source": "/repo/a"}})
        assert isinstance(flt, qm.Filter)
        assert flt.should is not None
        assert len(flt.should) == 1
        cond = flt.should[0]
        assert cond.key == "source"
        assert isinstance(cond.match, qm.MatchText)


class TestVectorStore:
    @pytest.mark.asyncio
    async def test_ensure_collection_creates_only_once(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = False

        store = VectorStore()
        await store.ensure_collection("coll")
        await store.ensure_collection("coll")

        assert client.create_collection.await_count == 1
        # source, document_hash, defines, language, parent_id, is_parent, is_child => 7
        assert client.create_payload_index.await_count == 7

    @pytest.mark.asyncio
    async def test_ensure_collection_skips_create_when_exists(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True

        store = VectorStore()
        await store.ensure_collection("coll")

        client.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upsert_chunks_validates_lengths(self, mock_async_client) -> None:
        store = VectorStore()

        with pytest.raises(ValueError):
            await store.upsert_chunks("c", vectors=[[0.0]], payloads=[], ids=["id1"])

    @pytest.mark.asyncio
    async def test_upsert_chunks_noop_on_empty(self, mock_async_client) -> None:
        client, _ = mock_async_client
        store = VectorStore()

        n = await store.upsert_chunks("c", vectors=[], payloads=[], ids=[])

        assert n == 0
        client.upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upsert_chunks_writes_points(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        store = VectorStore()

        n = await store.upsert_chunks(
            "c",
            vectors=[[0.1, 0.2]],
            payloads=[{"text": "x"}],
            ids=["id-1"],
        )

        assert n == 1
        client.upsert.assert_awaited_once()
        kwargs = client.upsert.await_args.kwargs
        assert kwargs["collection_name"] == "c"
        assert len(kwargs["points"]) == 1

    @pytest.mark.asyncio
    async def test_search_maps_points(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.return_value = SimpleNamespace(
            points=[
                SimpleNamespace(id="pid", score=0.9, payload={"text": "hi", "k": 1}),
            ]
        )
        store = VectorStore()

        hits = await store.search("c", [0.1, 0.2], top_k=3)

        assert len(hits) == 1
        assert hits[0].id == "pid"
        assert hits[0].score == pytest.approx(0.9)
        assert hits[0].text == "hi"
        assert hits[0].payload == {"text": "hi", "k": 1}

    @pytest.mark.asyncio
    async def test_search_returns_empty_on_404(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.side_effect = _make_unexpected_response(404)
        store = VectorStore()

        hits = await store.search("missing", [0.1])

        assert hits == []

    @pytest.mark.asyncio
    async def test_search_reraises_non_404(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.side_effect = _make_unexpected_response(500)
        store = VectorStore()

        with pytest.raises(UnexpectedResponse):
            await store.search("c", [0.1])

    @pytest.mark.asyncio
    async def test_search_returns_vector_when_requested(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.return_value = SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="pid",
                    score=0.9,
                    payload={"text": "hi"},
                    vector=[0.1, 0.2, 0.3],
                ),
            ]
        )
        store = VectorStore()

        hits = await store.search("c", [0.1, 0.2, 0.3], with_vectors=True)

        assert hits[0].vector == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]
        kwargs = client.query_points.await_args.kwargs
        assert kwargs["with_vectors"] is True

    @pytest.mark.asyncio
    async def test_search_named_vector_response_picks_dense(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.return_value = SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="pid",
                    score=0.9,
                    payload={"text": "hi"},
                    vector={"dense": [0.5, 0.6], "bm25": [9.0]},
                ),
            ]
        )
        store = VectorStore()

        hits = await store.search("c", [0.1], with_vectors=True)

        assert hits[0].vector == [pytest.approx(0.5), pytest.approx(0.6)]

    @pytest.mark.asyncio
    async def test_search_default_does_not_request_vectors(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search("c", [0.1])

        kwargs = client.query_points.await_args.kwargs
        assert kwargs["with_vectors"] is False

    @pytest.mark.asyncio
    async def test_search_with_filter_and_threshold(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.query_points.return_value = SimpleNamespace(points=[])
        store = VectorStore()

        await store.search(
            "c",
            [0.1],
            top_k=2,
            filter_obj={"language": "py"},
            score_threshold=0.5,
        )

        kwargs = client.query_points.await_args.kwargs
        assert kwargs["score_threshold"] == 0.5
        assert isinstance(kwargs["query_filter"], qm.Filter)

    @pytest.mark.asyncio
    async def test_delete_by_ids(self, mock_async_client) -> None:
        client, _ = mock_async_client
        store = VectorStore()

        deleted = await store.delete("c", ids=["a", "b"])

        assert deleted == 2
        client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_filter(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.count.return_value = SimpleNamespace(count=5)
        store = VectorStore()

        deleted = await store.delete("c", filter_obj={"language": "py"})

        assert deleted == 5
        client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_requires_ids_or_filter(self, mock_async_client) -> None:
        store = VectorStore()
        with pytest.raises(ValueError):
            await store.delete("c")

        with pytest.raises(ValueError):
            await store.delete("c", filter_obj={})

    @pytest.mark.asyncio
    async def test_fetch_payloads_by_ids(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.retrieve.return_value = [
            SimpleNamespace(id="a", payload={"text": "A"}),
            SimpleNamespace(id="b", payload={"text": "B"}),
        ]
        store = VectorStore()
        out = await store.fetch_payloads_by_ids("c", ["a", "b"])
        assert out == {"a": {"text": "A"}, "b": {"text": "B"}}

    @pytest.mark.asyncio
    async def test_count_returns_int(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.count.return_value = SimpleNamespace(count=42)
        store = VectorStore()

        assert await store.count("c") == 42

    @pytest.mark.asyncio
    async def test_count_returns_zero_on_404(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.count.side_effect = _make_unexpected_response(404)
        store = VectorStore()

        assert await store.count("c") == 0

    @pytest.mark.asyncio
    async def test_count_reraises_non_404(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.count.side_effect = _make_unexpected_response(500)
        store = VectorStore()

        with pytest.raises(UnexpectedResponse):
            await store.count("c")

    @pytest.mark.asyncio
    async def test_chunks_exist_false_when_collection_missing(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()

        assert (
            await store.chunks_exist("c", source="s", document_hash="h")
        ) is False

    @pytest.mark.asyncio
    async def test_chunks_exist_true_when_count_positive(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        client.count.return_value = SimpleNamespace(count=1)
        store = VectorStore()

        assert await store.chunks_exist("c", source="s", document_hash="h") is True

    @pytest.mark.asyncio
    async def test_chunks_exist_handles_unexpected_response(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.side_effect = _make_unexpected_response(404)
        store = VectorStore()

        assert (
            await store.chunks_exist("c", source="s", document_hash="h")
        ) is False

    @pytest.mark.asyncio
    async def test_list_collections_returns_sorted_names(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.get_collections.return_value = SimpleNamespace(
            collections=[SimpleNamespace(name="b"), SimpleNamespace(name="a")]
        )
        store = VectorStore()

        assert await store.list_collections() == ["a", "b"]

    @pytest.mark.asyncio
    async def test_create_collection_returns_false_when_exists(
        self, mock_async_client
    ) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        store = VectorStore()

        assert await store.create_collection("c") is False

    @pytest.mark.asyncio
    async def test_create_collection_returns_true_when_new(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()

        assert await store.create_collection("c") is True
        client.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stats_when_missing(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()

        info = await store.stats("c")

        assert info == {"collection": "c", "exists": False}

    @pytest.mark.asyncio
    async def test_stats_when_present(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        client.get_collection.return_value = SimpleNamespace(
            points_count=10,
            vectors_count=10,
            indexed_vectors_count=10,
            status=SimpleNamespace(value="green"),
        )
        store = VectorStore()

        info = await store.stats("c")

        assert info["exists"] is True
        assert info["points_count"] == 10
        assert info["status"] == "green"

    @pytest.mark.asyncio
    async def test_health_true_on_success(self, mock_async_client) -> None:
        store = VectorStore()
        assert await store.health() is True

    @pytest.mark.asyncio
    async def test_health_false_on_error(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.get_collections.side_effect = RuntimeError("nope")
        store = VectorStore()

        assert await store.health() is False

    @pytest.mark.asyncio
    async def test_aclose_closes_client(self, mock_async_client) -> None:
        client, _ = mock_async_client
        store = VectorStore()
        await store.aclose()
        client.close.assert_awaited_once()


class TestBumpFeedback:
    @pytest.mark.asyncio
    async def test_bumps_existing_feedback(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.retrieve.return_value = [
            SimpleNamespace(payload={"feedback": 2, "source": "f"})
        ]
        store = VectorStore()
        new_value = await store.bump_feedback("memory", "pid", 1)
        assert new_value == 3
        client.set_payload.assert_awaited_once()
        kwargs = client.set_payload.await_args.kwargs
        assert kwargs["payload"] == {"feedback": 3}
        assert kwargs["points"] == ["pid"]

    @pytest.mark.asyncio
    async def test_initializes_when_missing(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.retrieve.return_value = [SimpleNamespace(payload={})]
        store = VectorStore()
        new_value = await store.bump_feedback("memory", "pid", -1)
        assert new_value == -1

    @pytest.mark.asyncio
    async def test_returns_zero_on_missing_point(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.retrieve.return_value = []
        store = VectorStore()
        assert await store.bump_feedback("memory", "pid", 1) == 0
        client.set_payload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_zero_on_404_collection(self, mock_async_client) -> None:
        client, _ = mock_async_client
        client.retrieve.side_effect = _make_unexpected_response(404)
        store = VectorStore()
        assert await store.bump_feedback("memory", "pid", 1) == 0


class TestSweepExpired:
    @pytest.mark.asyncio
    async def test_returns_zero_when_collection_absent(
        self, mock_async_client
    ) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = False
        store = VectorStore()
        out = await store.sweep_expired("memory", now_iso="2026-05-26T10:00:00+00:00")
        assert out == 0
        client.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_delete_when_nothing_expired(
        self, mock_async_client
    ) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        client.count.return_value = SimpleNamespace(count=0)
        store = VectorStore()
        out = await store.sweep_expired("memory", now_iso="2026-05-26T10:00:00+00:00")
        assert out == 0
        client.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_expired_with_datetime_filter(
        self, mock_async_client
    ) -> None:
        client, _ = mock_async_client
        client.collection_exists.return_value = True
        client.count.return_value = SimpleNamespace(count=7)
        store = VectorStore()
        now = "2026-05-26T10:00:00+00:00"

        out = await store.sweep_expired("memory", now_iso=now)
        assert out == 7

        client.delete.assert_awaited_once()
        selector = client.delete.await_args.kwargs["points_selector"]
        assert isinstance(selector, qm.FilterSelector)
        cond = selector.filter.must[0]
        assert isinstance(cond, qm.FieldCondition)
        assert cond.key == "expires_at"
        assert isinstance(cond.range, qm.DatetimeRange)
        from datetime import datetime as _dt
        assert _dt.fromisoformat(now) == cond.range.lt


class TestProcessSingletons:
    @pytest.mark.asyncio
    async def test_get_and_shutdown_store(self, mock_async_client, mocker) -> None:
        from qilin import store as store_module

        mocker.patch.object(store_module, "_store", None)

        a = await store_module.get_store()
        b = await store_module.get_store()
        assert a is b

        await store_module.shutdown_store()
        assert store_module._store is None
