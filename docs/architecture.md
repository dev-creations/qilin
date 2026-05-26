# Qilin architecture

Qilin is a single Docker compose stack that exposes a Qdrant-backed vector
memory to AI clients over the Model Context Protocol. This document goes a bit
deeper than the top-level [README](../README.md) on how the pieces fit together
and where the seams are.

## Component map

```
+---------------------+        HTTPS / SSE         +-----------------------+
| MCP client (Cursor, |  <----------------------->  | qilin-mcp container   |
| Claude Desktop, ...)|     /sse, /mcp, /healthz   | (Starlette + FastMCP) |
+---------------------+                              +-----+--------+------+
                                                          |        |
                                       /api/embed (HTTP)  |        | gRPC/HTTP
                                                          v        v
                                              +---------------+ +------------+
                                              | Ollama (host) | |  Qdrant    |
                                              | nomic-embed-* | |  (vectors) |
                                              +---------------+ +------------+
```

The Go CLI on the host (`qilin`) generates and owns the compose file and the
TLS cert, but it does not sit in the request path.

## Process layout

- **`qilin-mcp` container.** Python 3.12, runs `uvicorn` against
  [`qilin.server:app`](../src/qilin/server.py). Holds two long-lived async
  clients: the Qdrant client in [`store.py`](../src/qilin/store.py) and the
  Ollama HTTP client in [`embeddings.py`](../src/qilin/embeddings.py). The
  lifespan hook tears both down on shutdown.
- **`qdrant` container.** Vanilla Qdrant. Cosine distance, one collection per
  namespace, two payload indexes per collection (`source`, `document_hash`).
- **Ollama on the host.** Reached over the loopback bridge
  (`host.docker.internal`). The container does not own the model.

## Request lifecycle: `remember`

```
client.remember(text)
       │
       ▼
tools.remember
       │  chunk_text(text)             ─► chunks with start_line/end_line
       │  embedder.embed(...)          ─► Ollama /api/embed
       │  build_payload(...)           ─► dict per chunk (text, lines, hash, ...)
       │  deterministic_point_id(...)  ─► uuid5(NS, source::hash::ordinal)
       ▼
store.upsert_chunks(collection, vectors, payloads, ids)
       │  ensure_collection on first use (creates collection + indexes)
       ▼
Qdrant upsert (wait=True)
```

The deterministic point ID is the linchpin: re-running `remember` with the same
`(source, text)` overwrites prior chunks in place rather than duplicating them.

## Request lifecycle: `recall`

```
client.recall(query, top_k=K, filter=…)
       │
       ▼
tools.recall
       │  embedder.embed([query], task=QUERY)
       ▼
store.search(collection, vector, top_k=K, filter_obj=…)
       │  qdrant.query_points(...)
       ▼
SearchHit list
       │  _format_hit -> flat dict per hit
       ▼
[{id, score, text, source, language, start_line, end_line, lines, …,
  extra_metadata: {...}}]
```

Since v1.0.0 the recall response is *flat*. See
[migrating-to-1.0.md](migrating-to-1.0.md) for the rationale.

## Chunking

[`chunking.chunk_text`](../src/qilin/chunking.py) walks the input as
*paragraph → sentence → token-window* atomic units. Each unit carries its
original line span (1-indexed, inclusive). When units are packed into a chunk,
the chunk's `start_line` is the minimum unit start and its `end_line` is the
maximum unit end. CRLF inputs are normalized to LF before line numbering, so
the line numbers a chunk advertises always match a `cat -n` of the original
file on a Unix host.

This is also why the payload carries `start_line`/`end_line`: clients can
render `src/foo.py:30-95` citations without re-tokenizing the source.

## Identity and idempotency

- **Point IDs:** `uuid5(POINT_ID_NAMESPACE, f"{source}::{sha256(text)}::{ordinal}")`.
  Re-running `remember(text, source=...)` with the same payload re-writes the
  same point IDs; a different `source` produces a different family of IDs.
- **Collections** are namespaces. There is no cross-collection deduplication.
- **CLI ingest** also checks `chunks_exist(source, document_hash)` before
  re-embedding a file, so re-running `qilin ingest <repo>` is fast and
  embedding-budget-aware.

## Where things live on disk

- `~/.qilin/config.json` - source of truth for runtime configuration.
- `~/.qilin/compose.yaml` - the docker compose file the CLI manages.
- `~/.qilin/certs/{cert.pem,key.pem}` - the self-signed TLS material.
- Qdrant data lives in the `qilin_qdrant_data` named volume by default.

## Transport

Today the server speaks MCP over Server-Sent Events at `/sse`. Plain HTTP is
bound to loopback only by default. A streamable HTTP transport at `/mcp` will
land in a later release; see the CHANGELOG.

## What's *not* in this picture

- Authentication on `/sse` (single-tenant by default; see the
  [LAN recipe](recipes/expose-on-lan.md) once that ships).
- Cross-collection deduplication or query federation.
- A custom UI; the [Qdrant dashboard](http://localhost:6333/dashboard) is the
  inspection surface.
