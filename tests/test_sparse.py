"""Tests for :mod:`qilin.sparse` lazy BM25 sparse encoder wrapper."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from qilin import sparse as sparse_module
from qilin.config import Settings
from qilin.sparse import SparseEmbedder, SparseVector


def test_embed_empty_list_returns_empty_list() -> None:
    s = SparseEmbedder(settings=Settings())
    assert s.embed([]) == []


def test_embed_one_returns_none_when_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)
    s = SparseEmbedder(settings=Settings())
    assert s.embed_one("hello") is None


def test_load_marks_unavailable_when_fastembed_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)
    s = SparseEmbedder(settings=Settings())
    assert s._load() is None
    assert s._unavailable is True
    assert s.available is False
    assert s.embed(["a"]) is None


def test_load_marks_unavailable_when_constructor_fails(monkeypatch) -> None:
    class Boom:
        def __init__(self, *_a, **_kw):
            raise RuntimeError("download failed")

    fake_mod = SimpleNamespace(SparseTextEmbedding=Boom)
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    s = SparseEmbedder(settings=Settings())
    assert s._load() is None
    assert s._unavailable is True


def test_embed_caches_model_after_first_load(monkeypatch) -> None:
    calls: list[int] = []

    class Encoder:
        def __init__(self, model_name: str) -> None:
            calls.append(1)

        def embed(self, texts):
            for _ in texts:
                yield SimpleNamespace(indices=[1, 2], values=[0.3, 0.7])

    fake_mod = SimpleNamespace(SparseTextEmbedding=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    s = SparseEmbedder(settings=Settings())
    s.embed(["a"])
    s.embed(["b"])
    assert len(calls) == 1


def test_embed_returns_sparse_vectors(monkeypatch) -> None:
    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def embed(self, texts):
            for _ in texts:
                yield SimpleNamespace(indices=[10, 20, 30], values=[0.1, 0.2, 0.3])

    fake_mod = SimpleNamespace(SparseTextEmbedding=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    s = SparseEmbedder(settings=Settings())
    out = s.embed(["hello", "world"])
    assert out is not None
    assert len(out) == 2
    assert isinstance(out[0], SparseVector)
    assert out[0].indices == [10, 20, 30]
    assert out[0].values == [0.1, 0.2, 0.3]


def test_embed_returns_none_when_embed_raises(monkeypatch) -> None:
    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def embed(self, texts):
            raise RuntimeError("boom")

    fake_mod = SimpleNamespace(SparseTextEmbedding=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    s = SparseEmbedder(settings=Settings())
    assert s.embed(["a"]) is None


def test_embed_one_returns_first_vector(monkeypatch) -> None:
    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def embed(self, texts):
            for _ in texts:
                yield SimpleNamespace(indices=[5], values=[0.9])

    fake_mod = SimpleNamespace(SparseTextEmbedding=Encoder)
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    s = SparseEmbedder(settings=Settings())
    out = s.embed_one("hi")
    assert isinstance(out, SparseVector)
    assert out.indices == [5]


@pytest.mark.asyncio
async def test_get_sparse_embedder_is_process_singleton(monkeypatch) -> None:
    monkeypatch.setattr(sparse_module, "_sparse", None)

    a = await sparse_module.get_sparse_embedder()
    b = await sparse_module.get_sparse_embedder()
    assert a is b

    await sparse_module.shutdown_sparse()
    assert sparse_module._sparse is None
