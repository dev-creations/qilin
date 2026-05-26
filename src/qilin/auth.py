"""Bearer-token auth middleware for the Starlette app.

When :attr:`Settings.auth_token` is unset, every request passes through
unauthenticated - that preserves the single-tenant localhost default. When
the token (or a list of tokens, for rotation) is set, every request to a
non-excluded path must carry ``Authorization: Bearer <token>`` matching one
of the configured tokens.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

# Paths that do *not* require a bearer token. Health and root keep the
# door open for `qilin doctor` / readiness probes; the rest is locked down.
DEFAULT_EXCLUDED_PATHS: frozenset[str] = frozenset({"/healthz", "/"})


def _normalize_tokens(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [t.strip() for t in value if t and t.strip()]


def _constant_time_in(candidate: str, tokens: Iterable[str]) -> bool:
    """Compare ``candidate`` against each token with constant-time equality."""
    matched = False
    for tok in tokens:
        if hmac.compare_digest(candidate, tok):
            matched = True
    return matched


class BearerAuthMiddleware:
    """ASGI middleware enforcing bearer-token auth on configured paths."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        tokens: str | list[str] | None,
        excluded_paths: Iterable[str] = DEFAULT_EXCLUDED_PATHS,
    ) -> None:
        self.app = app
        self.tokens = _normalize_tokens(tokens)
        self.excluded_paths = frozenset(excluded_paths)

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if not self.enabled or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.excluded_paths:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin1", errors="replace")
        if not raw or not raw.lower().startswith("bearer "):
            await _send_401(send, "missing bearer token")
            return

        candidate = raw[len("bearer ") :].strip()
        if not _constant_time_in(candidate, self.tokens):
            await _send_401(send, "invalid bearer token")
            return

        await self.app(scope, receive, send)


async def _send_401(send, message: str) -> None:
    body = json.dumps({"error": "unauthorized", "detail": message}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="qilin"'),
                (b"content-length", str(len(body)).encode("latin1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
