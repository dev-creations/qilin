# Changelog

All notable changes to Qilin are tracked here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Three new optional arguments on `recall` for post-processing the hit list:
  - `context_window: int` - fetch +/- N sibling chunks per hit (same `source`)
    and merge them into one contiguous text block. Merged hits also carry a
    `chunk_ordinals` array.
  - `group_by_source: bool` - keep at most one hit per `source`.
  - `mmr_lambda: float` - re-rank candidates by Maximal Marginal Relevance for
    diversity. ``1.0`` is plain similarity; ``0.5`` is balanced.
- `qilin recall` gained `--context-window`, `--group-by-source`, and `--mmr`
  flags that map onto the new tool arguments.
- `VectorStore.search` now accepts `with_vectors: bool` and `SearchHit`
  carries an optional ``vector`` field. ``VectorStore.fetch_neighbors``
  resolves sibling chunks by `(source, chunk_ordinal)` for the
  neighbor-expansion path.
- [Recipe: Tuning recall](docs/recipes/tuning-recall.md) walks through when
  to reach for each knob.
- Code-aware chunking via tree-sitter. `remember(text, language=...)` now
  routes Python, Go, JavaScript, TypeScript, TSX, and Rust through an AST
  splitter that emits one chunk per function/class/method. Code chunks carry:
  - `defines` - qualified symbol names declared in the chunk (KEYWORD-indexed
    so `filter={"defines": "MyClass"}` is fast).
  - `imports` - the file's import statements, duplicated onto every chunk.
  - `signature` - one-line preview of the first definition in the chunk.
  - `language` - the language hint (now a top-level KEYWORD-indexed payload
    field, supporting `filter={"language": "rust"}`).
- `qilin ingest` auto-detects each file's language from its extension and
  passes it to `remember`, so existing ingest commands pick up code-aware
  chunking with no flag change.
- New runtime dependencies: `tree-sitter>=0.25.0`,
  `tree-sitter-language-pack>=1.8.0`. Missing native wheels gracefully fall
  back to prose chunking with a logged warning.
- [Recipe: Code-aware search](docs/recipes/code-search.md) covers symbol
  filters, language scoping, and the supported language matrix.
- Hybrid retrieval (opt-in via `hybrid_enabled=true`). New collections are
  created with named dense + BM25 sparse vectors; `remember` writes both;
  `recall(mode="hybrid")` fuses them server-side with Qdrant's RRF. Falls
  back to dense automatically on old collections or when the sparse encoder
  is unavailable.
- Cross-encoder reranker (opt-in via `rerank_enabled=true` or per-call
  `rerank=true`). Pulls a `rerank_top_k`-sized candidate pool and reorders
  it with a FastEmbed `TextCrossEncoder` (default
  `Xenova/ms-marco-MiniLM-L-6-v2`).
- New MCP tool `recall_files(query, ...)`: groups recall hits by `source`,
  sums scores, returns one entry per file with a preview, line span,
  language, and hit count.
- `qilin.sparse.SparseEmbedder` and `qilin.reranker.Reranker` provide lazy,
  failure-tolerant wrappers around the FastEmbed models. Missing native
  wheels or model downloads degrade gracefully to dense-only retrieval.
- New runtime dependency `fastembed>=0.8.0`.
- [Recipe: Hybrid search and reranking](docs/recipes/hybrid-search.md) walks
  through how to enable both, when to pick each mode, and the cost profile.
- Incremental ingest with automatic stale-chunk cleanup: every `qilin ingest`
  run now scans the destination collection via `VectorStore.scan_sources`,
  re-embeds modified files, and deletes the previous content-hash chunks for
  the same source in the same pass. Skips unchanged files (the prior
  behavior) without any flag change.
- New `--prune` flag on `qilin ingest`: also forgets sources that no longer
  exist on disk, scoped to the active `--source-prefix` so it cannot wipe
  unrelated subtrees.
- New `qilin watch <path>` subcommand: live filesystem watcher powered by
  `watchfiles` with a configurable debounce (`--debounce-ms`, default 500).
  Re-ingests on save, forgets on delete, respects the same filters as
  `qilin ingest`.
- Per-collection chunker config via a new `collections: {name: {...}}`
  settings section. Each entry may set `chunk_size_tokens` and
  `chunk_overlap_tokens`; values are applied transparently through
  `tools.remember` so code and docs collections can have different
  granularity without per-call wiring. Per-collection embedder routing
  (different model/dim per collection) is intentionally deferred -
  documented as future work.
- New runtime dependency `watchfiles>=1.0.0`.
- Recipes:
  - [Incremental ingest](docs/recipes/incremental-ingest.md)
  - [Watch mode](docs/recipes/watch-mode.md)
  - [Code vs. docs collections](docs/recipes/code-vs-docs-collections.md)
- Bearer-token authentication via the new `auth_token` setting. Accepts a
  single token or a list (for zero-downtime rotation). When unset, the
  server is open to localhost as before. Implemented as a small Starlette
  middleware in `qilin.auth` with constant-time comparison; `/` and
  `/healthz` stay public for readiness probes.
- Streamable HTTP transport mounted at `/mcp` alongside the existing `/sse`
  endpoint. Newer MCP clients prefer streamable HTTP because it survives
  HTTP/2 multiplexing and corporate proxies. Toggle with the new
  `streamable_http_enabled` setting (default `true`).
- Payload-based TTL. `collections.<name>.ttl_seconds` makes `remember`
  attach an `expires_at` timestamp to every chunk. A background sweeper in
  the server runs every `ttl_sweep_seconds` (default `300`) and deletes
  expired points server-side via Qdrant's `DatetimeRange` filter.
- Recall analytics. Every `recall` call appends a JSONL event (query,
  collection, mode, top_k, rerank flag, latency, hit list) to
  `recall_log_path` (default `~/.qilin/logs/recall.jsonl`). Empty string
  disables logging.
- New CLI command `qilin recall-log [--since 1h] [-n 20] [--path ...]`
  tails the recall log with pretty formatting.
- New MCP tool `mark_useful(id, useful=True, collection=None)` writes a
  per-chunk `feedback` integer. The next `recall` applies a small score
  multiplier proportional to net feedback, capped at +/- 50%.
- New `VectorStore.bump_feedback` and `VectorStore.sweep_expired` helpers
  back the two new tools.
- Recipes:
  - [Exposing Qilin on the LAN](docs/recipes/expose-on-lan.md)
  - [Scratch vs. knowledge collections](docs/recipes/scratch-vs-knowledge-collections.md)
  - [Recall feedback loop](docs/recipes/recall-feedback-loop.md)

## [1.0.0]

The first stable release. Breaking change in the `recall` MCP tool response
shape; everything else is additive. See
[`docs/migrating-to-1.0.md`](docs/migrating-to-1.0.md) for the upgrade guide.

### Added

- Every chunk now carries `start_line` and `end_line` in its Qdrant payload
  (1-indexed, inclusive line numbers in the original input). `recall` hits
  surface them at the top level and render a compact `lines` string
  (`"30-95"`) for citation building.
- `docs/` tree: [architecture overview](docs/architecture.md), recipe index,
  and the [SSE-on-localhost-with-cert](docs/recipes/sse-on-localhost-with-cert.md)
  recipe.
- This `CHANGELOG.md`.

### Changed

- **Breaking:** `recall` hits are now flat. First-class fields (`source`,
  `language`, `start_line`, `end_line`, `lines`, `git_sha`, `chunk_ordinal`,
  `chunk_count`, `document_hash`, `created_at`) are promoted to the top level;
  caller-supplied metadata lives under `extra_metadata`. The previous nested
  `metadata` field is gone.
- `build_payload` accepts optional `start_line`/`end_line` arguments; existing
  callers continue to work because the new arguments default to `None`.

### Migration

- Re-ingest with `qilin ingest <path> --force` to backfill line spans on
  chunks created before 1.0; the deterministic ID scheme keeps that fast and
  duplicate-free.

## [0.x]

Pre-1.0 releases. The CLI, MCP server, chunker, and CI pipeline all landed in
this period. See git history for details.

[Unreleased]: https://github.com/dev-creations/qilin/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/dev-creations/qilin/releases/tag/v1.0.0
[0.x]: https://github.com/dev-creations/qilin/releases
