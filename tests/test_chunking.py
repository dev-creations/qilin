"""Tests for :mod:`qilin.chunking`."""

from __future__ import annotations

import pytest

from qilin.chunking import Chunk, chunk_text, count_tokens
from qilin.config import Settings


def test_count_tokens_empty_returns_zero() -> None:
    assert count_tokens("") == 0


def test_count_tokens_nonempty_is_positive() -> None:
    assert count_tokens("hello world") > 0


def test_chunk_text_empty_returns_empty_list() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t") == []


def test_chunk_text_short_input_yields_single_chunk() -> None:
    chunks = chunk_text("This is a short paragraph that fits in one window.")

    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].ordinal == 0
    assert chunks[0].token_count > 0
    assert "short paragraph" in chunks[0].text


def test_chunk_text_long_input_splits_into_multiple_chunks() -> None:
    paragraph = " ".join(f"word{i}" for i in range(800))
    text = (paragraph + "\n\n") * 3

    chunks = chunk_text(text, chunk_size_tokens=100, chunk_overlap_tokens=20)

    assert len(chunks) > 1
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.token_count <= 100 + 20 or chunk.token_count > 0


def test_chunk_text_prefers_paragraph_boundaries() -> None:
    para_a = "Alpha. " * 20
    para_b = "Beta. " * 20
    text = f"{para_a.strip()}\n\n{para_b.strip()}"

    chunks = chunk_text(text, chunk_size_tokens=80, chunk_overlap_tokens=0)

    joined = "\n".join(c.text for c in chunks)
    assert "Alpha" in joined
    assert "Beta" in joined


def test_chunk_text_overlap_clamped_when_geq_size() -> None:
    text = (" ".join(f"word{i}" for i in range(500))) + "."

    chunks_a = chunk_text(text, chunk_size_tokens=50, chunk_overlap_tokens=50)
    chunks_b = chunk_text(text, chunk_size_tokens=50, chunk_overlap_tokens=100)

    assert chunks_a
    assert chunks_b
    assert len(chunks_a) > 1
    assert len(chunks_b) > 1


def test_chunk_text_uses_settings_when_overrides_omitted(settings_factory) -> None:
    settings: Settings = settings_factory(chunk_size_tokens=64, chunk_overlap_tokens=8)
    text = " ".join(f"word{i}" for i in range(400)) + "."

    chunks = chunk_text(text, settings=settings)

    assert len(chunks) > 1
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_chunk_text_hard_splits_oversized_sentence() -> None:
    big_sentence = " ".join(f"tok{i}" for i in range(500)) + "."

    chunks = chunk_text(big_sentence, chunk_size_tokens=60, chunk_overlap_tokens=0)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.strip()


def test_chunk_text_normalizes_crlf_and_extra_whitespace() -> None:
    text = "Line one.\r\n\r\nLine    two.\tEnd."

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert "\r" not in chunks[0].text
    assert "  " not in chunks[0].text.replace("\n\n", "")


@pytest.mark.parametrize("size, overlap", [(64, 0), (128, 16), (200, 50)])
def test_chunk_text_respects_size_parameter(size: int, overlap: int) -> None:
    text = " ".join(f"word{i}" for i in range(600)) + "."

    chunks = chunk_text(text, chunk_size_tokens=size, chunk_overlap_tokens=overlap)

    assert chunks
    for chunk in chunks:
        assert chunk.token_count > 0
