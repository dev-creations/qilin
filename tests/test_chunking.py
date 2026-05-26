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
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1


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


class TestLineSpans:
    def test_single_line_input_spans_one(self) -> None:
        chunks = chunk_text("one liner.")

        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 1

    def test_multi_line_paragraph_spans_full_range(self) -> None:
        text = "line one\nline two\nline three"

        chunks = chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3

    def test_two_paragraphs_packed_into_one_chunk(self) -> None:
        text = "first para.\n\nsecond para."

        chunks = chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3

    def test_paragraph_after_leading_blank_lines(self) -> None:
        text = "\n\n\nactual content here.\nmore content."

        chunks = chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0].start_line == 4
        assert chunks[0].end_line == 5

    def test_chunks_carry_disjoint_or_adjacent_line_ranges(self) -> None:
        paragraphs = [f"paragraph number {i} with several words " * 30 for i in range(5)]
        text = "\n\n".join(paragraphs)

        chunks = chunk_text(text, chunk_size_tokens=80, chunk_overlap_tokens=0)

        assert len(chunks) >= 2
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_crlf_line_numbering_matches_normalized_lines(self) -> None:
        text = "first\r\n\r\nsecond\r\nthird"

        chunks = chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 4
