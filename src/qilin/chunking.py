"""Token-aware chunking for inputs that exceed the embedding model's context window.

The Nomic v2 embedder has a 512-token context limit. We default to ~450-token
windows with 50-token overlap (configurable via ``Settings``) and try to break on
paragraph or sentence boundaries before falling back to a hard token cut.

Every emitted :class:`Chunk` carries the ``start_line``/``end_line`` span (1-indexed,
inclusive) it covers in the *original* input. Callers can use that to build
citations like ``src/foo.py:30-95`` without having to re-tokenize the source.

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
_WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """A single chunk ready to be embedded and stored.

    ``start_line`` and ``end_line`` are 1-indexed inclusive line numbers in the
    *original* input. Multi-paragraph chunks span from the first line of their
    first paragraph to the last line of their last paragraph.

    Code-aware chunks additionally carry symbol metadata:

    - ``defines``: qualified names of definitions (functions, classes,
      methods) declared inside the chunk.
    - ``imports``: import statements visible at the top of the file, attached
      to every chunk for retrieval context.
    - ``signature``: a one-line preview of the first definition in the chunk,
      handy for hover-style displays.

    These default to empty/None for prose chunks produced by :func:`chunk_text`.
    """

    text: str
    ordinal: int
    token_count: int
    start_line: int
    end_line: int
    defines: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class _AtomicUnit:
    """An internal pre-chunk unit (paragraph / sentence / hard-token slice).

    Carries the original line span so chunks can preserve it.
    """

    text: str
    token_count: int
    start_line: int
    end_line: int


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the cl100k_base token count for ``text``."""
    if not text:
        return 0
    return len(_encoder().encode(text, disallowed_special=()))


def _iter_paragraphs_with_lines(text: str) -> Iterable[_AtomicUnit]:
    """Yield paragraph-level atomic units with original line spans.

    A paragraph is a maximal block of non-blank lines separated by blank lines.
    Internal whitespace inside each line is collapsed; CRLF is normalized to LF.
    Line numbers are 1-indexed in the original (post-CRLF-normalization) input.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return
    lines = text.split("\n")
    current: list[str] = []
    start: int | None = None
    last_nonblank: int = 0
    for i, raw in enumerate(lines, start=1):
        stripped = _WHITESPACE_RE.sub(" ", raw).strip()
        if not stripped:
            if current:
                joined = "\n".join(current).strip()
                if joined:
                    yield _AtomicUnit(
                        text=joined,
                        token_count=count_tokens(joined),
                        start_line=start or 1,
                        end_line=last_nonblank,
                    )
                current = []
                start = None
            continue
        if start is None:
            start = i
        current.append(stripped)
        last_nonblank = i
    if current:
        joined = "\n".join(current).strip()
        if joined:
            yield _AtomicUnit(
                text=joined,
                token_count=count_tokens(joined),
                start_line=start or 1,
                end_line=last_nonblank,
            )


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


def _atomic_units(text: str, max_tokens: int) -> Iterable[_AtomicUnit]:
    """Yield atomic units that each fit in ``max_tokens``.

    Paragraphs that are already short pass through unchanged. Long paragraphs
    are broken on sentence boundaries, then hard-split by tokens as a last
    resort. Sub-paragraph units inherit their parent paragraph's line span.
    """
    for para in _iter_paragraphs_with_lines(text):
        if para.token_count <= max_tokens:
            yield para
            continue
        for sentence in _split_sentences(para.text):
            sent_tokens = count_tokens(sentence)
            if sent_tokens <= max_tokens:
                yield _AtomicUnit(
                    text=sentence,
                    token_count=sent_tokens,
                    start_line=para.start_line,
                    end_line=para.end_line,
                )
                continue
            for piece in _hard_split_by_tokens(sentence, max_tokens):
                yield _AtomicUnit(
                    text=piece,
                    token_count=count_tokens(piece),
                    start_line=para.start_line,
                    end_line=para.end_line,
                )


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
    spans boundaries. Each returned :class:`Chunk` carries the original
    ``start_line``/``end_line`` span (1-indexed, inclusive).
    """
    settings = settings or get_settings()
    size = chunk_size_tokens or settings.chunk_size_tokens
    overlap = chunk_overlap_tokens or settings.chunk_overlap_tokens
    if overlap >= size:
        overlap = max(0, size // 5)

    chunks: list[Chunk] = []
    current_units: list[_AtomicUnit] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current_units, current_tokens
        if not current_units:
            return
        joined = "\n\n".join(u.text for u in current_units).strip()
        if not joined:
            current_units = []
            current_tokens = 0
            return
        start_line = min(u.start_line for u in current_units)
        end_line = max(u.end_line for u in current_units)
        chunks.append(
            Chunk(
                text=joined,
                ordinal=len(chunks),
                token_count=current_tokens,
                start_line=start_line,
                end_line=end_line,
            )
        )
        if overlap <= 0:
            current_units = []
            current_tokens = 0
            return
        tail: list[_AtomicUnit] = []
        tail_tokens = 0
        for unit in reversed(current_units):
            if tail_tokens + unit.token_count > overlap and tail:
                break
            tail.append(unit)
            tail_tokens += unit.token_count
        current_units = list(reversed(tail))
        current_tokens = tail_tokens

    for unit in _atomic_units(text, size):
        if current_tokens + unit.token_count > size and current_units:
            flush()
        current_units.append(unit)
        current_tokens += unit.token_count

    flush()
    return chunks
