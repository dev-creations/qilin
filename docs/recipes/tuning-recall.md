# Recipe: Tuning recall

**Goal:** get higher-quality `recall` results without changing your embeddings.

Qilin's `recall` tool takes three optional post-processing knobs:

| Knob | What it does |
|---|---|
| `context_window=N` | Around each hit, fetch the `N` chunks before and `N` after (same `source`) and merge them into one contiguous block. |
| `group_by_source=True` | Return at most one hit per `source` (the highest-scoring one). |
| `mmr_lambda=0..1` | Re-rank the candidate pool by Maximal Marginal Relevance. Lower values favor diverse hits over near-duplicates. |

They're independent. You can mix them freely.

## When to use what

### `context_window`

Use it when the chunker has split a logical unit (a function, a long
paragraph, an argument list) across two adjacent chunks. The merged block
gives the agent enough surrounding context to answer questions without
having to call `recall` again.

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "how do we handle Ollama timeouts?",
    "top_k": 3,
    "context_window": 1
  }
}
```

Each hit comes back with `text` joined from the merged siblings, a widened
`lines`/`start_line`/`end_line` span, and a new `chunk_ordinals` array
listing the ordinals that were merged.

Overlapping neighborhoods are deduplicated: if two raw hits would expand into
overlapping ranges in the same source, only the higher-scoring one survives.

### `group_by_source`

Use it when a query is likely to lock onto a single file and you'd rather see
one expanded snippet per file than five neighbors of the same chunk.

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "deterministic point id derivation",
    "top_k": 5,
    "group_by_source": true,
    "context_window": 2
  }
}
```

That pattern - `group_by_source` plus a small `context_window` - is the
sweet spot for "where in the codebase is this?" style queries.

### `mmr_lambda`

Use it when raw vector search keeps returning near-duplicates. MMR penalizes
candidates that are too similar to already-selected hits.

| `mmr_lambda` | Behavior |
|---|---|
| `1.0` | Pure cosine to query - effectively off. |
| `0.7` | Slight diversity boost. Reasonable default. |
| `0.5` | Balanced. Good when results feel monotonous. |
| `0.3` | Heavily favors diversity; useful for exploratory queries. |

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "error handling",
    "top_k": 5,
    "mmr_lambda": 0.5
  }
}
```

Under the hood Qilin requests the stored vectors back from Qdrant (cheap for
top-K=25, the internal candidate pool size), runs MMR client-side, and then
slices to `top_k`.

## Combining all three

A query that benefits from all three knobs - "give me one diversified, fully
expanded hit per relevant file":

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "vector store filter syntax",
    "top_k": 5,
    "mmr_lambda": 0.6,
    "group_by_source": true,
    "context_window": 2
  }
}
```

Order of operations inside Qilin:

1. Fetch an enlarged candidate pool (`top_k * 4`, min 25) from Qdrant.
2. Apply MMR if `mmr_lambda` is set.
3. Apply `group_by_source` to collapse to one-per-source.
4. Truncate to `top_k`.
5. Expand each surviving hit with `context_window` siblings.

## From the CLI

The three knobs also show up on `qilin recall`:

```bash
qilin recall "deterministic point id" \
  --top-k 5 \
  --context-window 2 \
  --group-by-source \
  --mmr 0.6
```

The displayed citation suffix (`a.py:30-95`) reflects the *expanded* line
span when `--context-window` is in play.

## Cost notes

- `context_window=N` triggers one `scroll` request per unique surviving hit.
  For `top_k=5, N=2` that's at most 5 cheap range queries against the
  payload-indexed `source` field.
- `mmr_lambda` makes Qilin pull stored vectors back from Qdrant for the
  internal candidate pool (25 by default). For 768-dim vectors that's ~75KB
  per recall - negligible over loopback, noticeable over a slow WAN.
- `group_by_source` is pure post-filter; free.

## Caveats

- Old chunks (ingested before 1.0) don't carry `start_line`/`end_line`. They
  still show up in expanded blocks; their merged span will just omit those
  fields. Re-ingest with `qilin ingest <path> --force` to backfill.
- `context_window` only merges across the same `source`. Chunks ingested
  without a source (raw `remember(text)`) won't be expanded.
- MMR diversifies; it doesn't *cluster*. For tight grouping use
  `group_by_source` (or, when it lands, the upcoming `recall_files` tool).
