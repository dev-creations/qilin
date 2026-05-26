"""Tests for :mod:`qilin.reranker` lazy cross-encoder wrapper."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qilin import reranker as reranker_module
from qilin.config import Settings
from qilin.reranker import Reranker


def test_score_empty_documents_returns_empty_list() -> None:
    r = Reranker(settings=Settings())
    assert r.score("q", []) == []


def test_load_marks_unavailable_when_fastembed_missing(monkeypatch) -> None:
    """The import-time fallback path: pretend fastembed isn't installed."""
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", None)

    r = Reranker(settings=Settings())
    assert r._load() is None
    assert r._unavailable is True
    assert r.available is False
    assert r.score("q", ["doc"]) is None


def test_load_marks_unavailable_when_constructor_fails(monkeypatch) -> None:
    """Constructor raising should not propagate; just disable the feature."""

    class BoomEncoder:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("model download failed")

    fake_mod = SimpleNamespace(TextCrossEncoder=BoomEncoder)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake_mod)

    r = Reranker(settings=Settings())
    assert r._load() is None
    assert r._unavailable is True


def test_load_caches_model_after_first_success(monkeypatch) -> None:
    instances: list[MagicMock] = []

    class Encoder:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name
            instances.append(self)  # type: ignore[arg-type]

        def rerank(self, query: str, docs: list[str]):
            return [0.7, 0.4]

    fake_mod = SimpleNamespace(TextCrossEncoder=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake_mod)

    r = Reranker(settings=Settings())
    assert r.available is True
    assert r.available is True
    assert len(instances) == 1, "model must be constructed only once"


def test_score_returns_floats(monkeypatch) -> None:
    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def rerank(self, query: str, docs: list[str]):
            return [0.9, 0.1, 0.5]

    fake_mod = SimpleNamespace(TextCrossEncoder=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake_mod)

    r = Reranker(settings=Settings())
    scores = r.score("q", ["a", "b", "c"])
    assert scores == [0.9, 0.1, 0.5]
    assert all(isinstance(s, float) for s in scores)


def test_score_returns_none_when_rerank_raises(monkeypatch) -> None:
    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def rerank(self, query: str, docs: list[str]):
            raise RuntimeError("inference exploded")

    fake_mod = SimpleNamespace(TextCrossEncoder=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake_mod)

    r = Reranker(settings=Settings())
    assert r.score("q", ["a"]) is None


@pytest.mark.asyncio
async def test_get_reranker_is_process_singleton(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module, "_reranker", None)

    a = await reranker_module.get_reranker()
    b = await reranker_module.get_reranker()
    assert a is b

    await reranker_module.shutdown_reranker()
    assert reranker_module._reranker is None
