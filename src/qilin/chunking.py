"""Token-aware chunking for inputs that exceed the embedding model's context window.

The Nomic v2 embedder has a 512-token context limit. We default to ~450-token
windows with 50-token overlap (configurable via ``Settings``) and try to break on
paragraph or sentence boundaries before falling back to a hard token cut.

We use ``tiktoken``'s ``cl100k_base`` as a stable, fast proxy tokenizer; it does
not exactly match the model's BPE but it is consistently close enough that the
chunk boundaries we produce stay safely under the 512-token model limit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from .config import Settings, get_settings

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.!?])\s+(?=[A-Z0-9\"'(\[])")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")
_WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """A single chunk ready to be embedded and stored."""

    text: str
    ordinal: int
    token_count: int


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the cl100k_base token count for ``text``."""
    if not text:
        return 0
    return len(_encoder().encode(text, disallowed_special=()))


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]


def _split_sentences(paragraph: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def _hard_split_by_tokens(text: str, max_tokens: int) -> list[str]:
    """Break ``text`` into <=``max_tokens`` slices by decoding token windows."""
    enc = _encoder()
    token_ids = enc.encode(text, disallowed_special=())
    out: list[str] = []
    for start in range(0, len(token_ids), max_tokens):
        window = token_ids[start : start + max_tokens]
        out.append(enc.decode(window).strip())
    return [s for s in out if s]


def _atomic_units(text: str, max_tokens: int) -> Iterable[tuple[str, int]]:
    """Yield ``(unit_text, token_count)`` pairs where each unit fits in ``max_tokens``."""
    enc = _encoder()
    for paragraph in _split_paragraphs(text):
        if len(enc.encode(paragraph, disallowed_special=())) <= max_tokens:
            yield paragraph, len(enc.encode(paragraph, disallowed_special=()))
            continue
        for sentence in _split_sentences(paragraph):
            if len(enc.encode(sentence, disallowed_special=())) <= max_tokens:
                yield sentence, len(enc.encode(sentence, disallowed_special=()))
            else:
                for piece in _hard_split_by_tokens(sentence, max_tokens):
                    yield piece, len(enc.encode(piece, disallowed_special=()))


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int | None = None,
    chunk_overlap_tokens: int | None = None,
    settings: Settings | None = None,
) -> list[Chunk]:
    """Chunk ``text`` into windows that fit the embedding model's context.

    Greedily packs paragraph- and sentence-sized atomic units into windows of up
    to ``chunk_size_tokens`` tokens; when a window is closed we carry roughly
    ``chunk_overlap_tokens`` of trailing text into the next window so context
    spans boundaries.
    """
    settings = settings or get_settings()
    size = chunk_size_tokens or settings.chunk_size_tokens
    overlap = chunk_overlap_tokens or settings.chunk_overlap_tokens
    if overlap >= size:
        overlap = max(0, size // 5)

    normalized = _normalize(text)
    if not normalized:
        return []

    chunks: list[Chunk] = []
    current_units: list[tuple[str, int]] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current_units, current_tokens
        if not current_units:
            return
        joined = "\n\n".join(u for u, _ in current_units).strip()
        chunks.append(
            Chunk(text=joined, ordinal=len(chunks), token_count=current_tokens)
        )
        if overlap <= 0:
            current_units = []
            current_tokens = 0
            return
        tail: list[tuple[str, int]] = []
        tail_tokens = 0
        for unit_text, unit_tokens in reversed(current_units):
            if tail_tokens + unit_tokens > overlap and tail:
                break
            tail.append((unit_text, unit_tokens))
            tail_tokens += unit_tokens
        current_units = list(reversed(tail))
        current_tokens = tail_tokens

    for unit_text, unit_tokens in _atomic_units(normalized, size):
        if current_tokens + unit_tokens > size and current_units:
            flush()
        current_units.append((unit_text, unit_tokens))
        current_tokens += unit_tokens

    flush()
    return chunks
