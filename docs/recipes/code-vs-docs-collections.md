# Recipe: Code vs. docs collections

**Goal:** put code and prose in two different collections with different
chunker settings, then query them separately depending on the question.

## Why bother

The same global `chunk_size_tokens` (default 450) doesn't fit both well:

- **Code** chunks better around AST boundaries. Whole functions are usually
  small enough to fit, so a *smaller* chunk size (200-300 tokens) yields
  finer-grained retrieval and tighter `recall` results.
- **Prose** benefits from larger windows (~500 tokens) so paragraphs aren't
  fragmented.

Mixing them in one collection means you compromise on both.

## The split

Two collections, each with its own override:

```bash
qilin ingest /repo                    --collection code \
    --include .py --include .go --include .rs --include .ts --include .tsx \
    --include .js
qilin ingest /repo/docs               --collection docs
```

And the per-collection config in `~/.qilin/config.json` (or via env vars):

```json
{
  "collections": {
    "code": {
      "chunk_size_tokens": 220,
      "chunk_overlap_tokens": 20
    },
    "docs": {
      "chunk_size_tokens": 500,
      "chunk_overlap_tokens": 60
    }
  }
}
```

Both collections reuse the global embedding model and dimension; only the
chunker tuning changes per collection.

> Per-collection *embedding-model* routing (e.g. running `nomic-embed-code`
> on `code` and `nomic-embed-text` on `docs`) is on the roadmap but not yet
> shipped because it requires per-collection vector-dimension config.

## Querying

The cheap routing is to pick the collection per query:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "how does the Counter class increment work?",
    "collection": "code",
    "context_window": 1
  }
}
```

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "how do I deploy Qilin behind a reverse proxy?",
    "collection": "docs"
  }
}
```

For "ask both" workflows, a wrapping LLM agent can dispatch two `recall`
calls and merge the results. There's no built-in fan-out yet.

## Pairing with code-aware chunking

If you ingest the `code` collection with file extensions that match the
[code-aware chunker](code-search.md), you get the best of both worlds:

- AST-aligned chunk boundaries.
- Small chunks (one function each) thanks to the 220-token override.
- `defines` filters for symbol-scoped queries.

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "increment semantics",
    "collection": "code",
    "filter": { "defines": "Counter.increment" }
  }
}
```

## Setting it up from scratch

The two-collection layout is best baked into your `qilin init` flow. After
init, write the per-collection config:

```bash
qilin config set collections.code.chunk_size_tokens 220
qilin config set collections.code.chunk_overlap_tokens 20
qilin config set collections.docs.chunk_size_tokens 500
qilin config set collections.docs.chunk_overlap_tokens 60
qilin down && qilin up
```

(`qilin config set` is from the Go CLI; the JSON it writes lives at
`~/.qilin/config.json` and is read by the server on startup.)

Then ingest each side:

```bash
qilin ingest /repo --collection code --include .py --include .go --include .ts
qilin ingest /repo/docs --collection docs
```

Pair with [`qilin watch`](watch-mode.md) to keep both in sync as you edit.

## Caveats

- Two collections double the storage. For a repo with 100k chunks total,
  that's negligible (~1 GB Qdrant footprint with 768-dim vectors).
- The Qdrant collection-creation overhead is one-time. Subsequent ingests
  are no-ops until content changes.
- Per-collection chunker config does *not* yet support per-language
  overrides within one collection (e.g. "in `code`, use chunk_size 220 for
  Python and 350 for Rust"). For now, split into more collections if you
  need that granularity.
