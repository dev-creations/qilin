"""Tests for :mod:`qilin.analytics` recall log + feedback boost."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qilin import analytics
from qilin.store import SearchHit


def test_resolve_log_path_default() -> None:
    p = analytics.resolve_log_path(None)
    assert p is not None
    assert p.name == "recall.jsonl"


def test_resolve_log_path_disabled_by_empty_string() -> None:
    assert analytics.resolve_log_path("") is None
    assert analytics.resolve_log_path("   ") is None


def test_resolve_log_path_expands_user(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    p = analytics.resolve_log_path("~/qlog.jsonl")
    assert str(tmp_path) in str(p)


def test_log_recall_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "recall.jsonl"
    analytics.log_recall(
        path,
        query="who",
        collection="memory",
        top_k=3,
        mode="hybrid",
        rerank=True,
        latency_ms=12.345,
        hits=[
            {"id": "a", "score": 0.9, "source": "f.py", "lines": "1-5"},
            {"id": "b", "score": 0.8, "source": "g.py", "lines": "6-7"},
        ],
    )
    analytics.log_recall(
        path,
        query="more",
        collection="memory",
        top_k=1,
        mode="dense",
        rerank=False,
        latency_ms=3.0,
        hits=[],
    )

    text = path.read_text(encoding="utf-8").splitlines()
    assert len(text) == 2
    first = json.loads(text[0])
    assert first["query"] == "who"
    assert first["mode"] == "hybrid"
    assert first["rerank"] is True
    assert first["latency_ms"] == 12.35
    assert len(first["hits"]) == 2
    assert first["hits"][0]["id"] == "a"


def test_log_recall_noop_on_none_path(tmp_path: Path) -> None:
    analytics.log_recall(
        None,
        query="x",
        collection="m",
        top_k=1,
        mode="dense",
        rerank=False,
        latency_ms=1.0,
        hits=[],
    )


def test_log_recall_swallows_oserror(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "no" / "such" / "recall.jsonl"

    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    analytics.log_recall(
        path,
        query="x",
        collection="m",
        top_k=1,
        mode="dense",
        rerank=False,
        latency_ms=1.0,
        hits=[],
    )


def test_apply_feedback_boosts_upvoted_hits() -> None:
    hits = [
        SearchHit(
            id="a", score=0.5, text="a", payload={"feedback": 4}, vector=None
        ),
        SearchHit(
            id="b", score=0.6, text="b", payload={"feedback": 0}, vector=None
        ),
        SearchHit(id="c", score=0.55, text="c", payload={}, vector=None),
    ]
    out = analytics.apply_feedback(hits)
    by_id = {h.id: h for h in out}
    assert by_id["a"].score > 0.5
    assert by_id["b"].score == 0.6
    assert by_id["c"].score == 0.55
    assert out[0].id == "a"


def test_apply_feedback_cap_on_extreme_votes() -> None:
    hits = [SearchHit(id="a", score=1.0, text="", payload={"feedback": 1000}, vector=None)]
    out = analytics.apply_feedback(hits)
    assert out[0].score == 1.0 * analytics.MAX_FEEDBACK_BOOST


def test_apply_feedback_penalises_downvotes() -> None:
    hits = [
        SearchHit(id="a", score=1.0, text="", payload={"feedback": -2}, vector=None),
        SearchHit(id="b", score=0.5, text="", payload={"feedback": 0}, vector=None),
    ]
    out = analytics.apply_feedback(hits)
    by_id = {h.id: h for h in out}
    assert by_id["a"].score < 1.0


def test_iter_log_events_filters_by_since(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    now = datetime.now(UTC)
    older = now - timedelta(hours=2)
    newer = now - timedelta(minutes=5)
    path.write_text(
        "\n".join(
            json.dumps({"ts": ts.isoformat(), "query": q})
            for ts, q in [(older, "old"), (newer, "new")]
        ),
        encoding="utf-8",
    )

    cutoff = (now - timedelta(hours=1)).timestamp()
    events = list(analytics.iter_log_events(path, since=cutoff))
    assert [e["query"] for e in events] == ["new"]


def test_iter_log_events_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text("{not json}\n" + json.dumps({"query": "ok"}) + "\n", encoding="utf-8")
    events = list(analytics.iter_log_events(path))
    assert events == [{"query": "ok"}]


def test_iter_log_events_empty_when_no_file(tmp_path: Path) -> None:
    events = list(analytics.iter_log_events(tmp_path / "missing.jsonl"))
    assert events == []
