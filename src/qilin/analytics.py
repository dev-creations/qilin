"""JSON-lines recall analytics + feedback boost.

Every ``recall`` call appends a structured event to the configured log file.
The ``qilin recall-log`` subcommand reads it back. The :func:`apply_feedback`
helper applies a soft score boost based on the per-chunk ``feedback`` field
that ``mark_useful`` writes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH: Path = Path.home() / ".qilin" / "logs" / "recall.jsonl"
MAX_FEEDBACK_BOOST = 1.5
FEEDBACK_BOOST_PER_VOTE = 0.05


def resolve_log_path(configured: str | None) -> Path | None:
    """Return the resolved recall-log path, or None if disabled.

    Empty string explicitly disables logging; None falls back to the default.
    """
    if configured is None:
        return DEFAULT_LOG_PATH
    if not configured.strip():
        return None
    return Path(configured).expanduser()


def log_recall(
    path: Path | None,
    *,
    query: str,
    collection: str,
    top_k: int,
    mode: str,
    rerank: bool,
    latency_ms: float,
    hits: list[dict[str, Any]],
) -> None:
    """Append one recall event to the JSONL log.

    Best-effort: any I/O failure is logged at WARNING level and swallowed so
    a busted log directory never breaks user-facing recall.
    """
    if path is None:
        return
    event = {
        "ts": datetime.now(UTC).isoformat(),
        "query": query,
        "collection": collection,
        "top_k": top_k,
        "mode": mode,
        "rerank": rerank,
        "latency_ms": round(float(latency_ms), 2),
        "hits": [
            {
                "id": h.get("id"),
                "score": float(h.get("score", 0.0)),
                "source": h.get("source"),
                "lines": h.get("lines"),
            }
            for h in hits
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("recall log write to %s failed: %s", path, exc)


def apply_feedback(hits: list[Any]) -> list[Any]:
    """Boost SearchHit scores using the stored ``feedback`` payload field.

    Each net upvote multiplies score by ``1 + FEEDBACK_BOOST_PER_VOTE``,
    capped at :data:`MAX_FEEDBACK_BOOST`. Downvotes shrink it symmetrically.
    Hits without feedback pass through unchanged.
    """
    out = []
    for h in hits:
        payload = getattr(h, "payload", {}) or {}
        feedback = payload.get("feedback")
        if not isinstance(feedback, int) or feedback == 0:
            out.append(h)
            continue
        multiplier = max(
            1.0 / MAX_FEEDBACK_BOOST,
            min(MAX_FEEDBACK_BOOST, 1.0 + FEEDBACK_BOOST_PER_VOTE * feedback),
        )
        try:
            new_hit = h.__class__(
                id=h.id,
                score=float(h.score) * multiplier,
                text=h.text,
                payload=h.payload,
                vector=getattr(h, "vector", None),
            )
        except TypeError:
            new_hit = h
        out.append(new_hit)
    out.sort(key=lambda x: getattr(x, "score", 0.0), reverse=True)
    return out


def iter_log_events(path: Path, *, since: float | None = None):
    """Yield decoded events from the recall log file, optionally filtered by ts.

    ``since`` is a unix timestamp; events with ``ts`` older than that are
    skipped. Lines that fail to decode are silently skipped (we don't want a
    corrupt log to break ``qilin recall-log``).
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                ts = event.get("ts")
                if ts:
                    try:
                        event_ts = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        ).timestamp()
                    except ValueError:
                        event_ts = None
                    if event_ts is not None and event_ts < since:
                        continue
            yield event


class Clock:
    """Tiny monotonic-clock wrapper so tests can fake elapsed times."""

    @staticmethod
    def now_ms() -> float:
        return time.monotonic() * 1000.0


# Allow tests to monkeypatch the module-level os interface.
_os = os
