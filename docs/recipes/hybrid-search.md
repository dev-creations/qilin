# Recipe: Hybrid search and reranking

**Goal:** mix lexical (BM25) and semantic (dense vector) signals, optionally
followed by a cross-encoder reranker, to get noticeably better results for
mixed-style queries:

- *lexical-heavy*: "find every place that raises `EmbeddingError`"
- *semantic-heavy*: "how do we handle Ollama timeouts?"
- *mixed*: "where do we parse the `chunk_size_tokens` setting?"

Pure dense vectors lose to BM25 on the first; pure BM25 loses on the second;
hybrid + a reranker is the best of both.

## What ships with this

Two opt-in features:

| Feature | Setting | Default | Cost |
|---|---|---|---|
| Hybrid retrieval (dense + BM25, fused with RRF) | `hybrid_enabled` | `false` | New collections use named vectors. Sparse vectors are tokenized client-side with [FastEmbed](https://github.com/qdrant/fastembed). |
| Cross-encoder reranker | `rerank_enabled` | `false` | A ~80 MB ONNX model downloads on first use. Each rerank scores `rerank_top_k` (default 50) candidates locally. |

Both are off by default so an upgrade from 1.x is non-breaking. Flip them on
when you want the quality jump.

## Turning hybrid on

```bash
qilin config set hybrid_enabled true
qilin down && qilin up
```

What that changes:

- New collections are created with named dense + sparse-BM25 vectors. The
  sparse-vector config uses Qdrant's IDF modifier, so BM25 scoring runs
  server-side.
- `recall` defaults to `mode="hybrid"`, fusing dense and sparse results with
  RRF (Reciprocal Rank Fusion).
- Every `remember` call computes a sparse vector alongside the dense one,
  via FastEmbed's `SparseTextEmbedding`.
- Existing collections (created before you flipped the flag) keep working in
  dense-only mode. To migrate them, re-create the collection and re-ingest:

  ```bash
  qilin recall  # confirm the data is no longer urgent
  # then in the Qdrant dashboard or via the qdrant client: delete the collection
  qilin ingest /path/to/repo --collection myrepo
  ```

## Querying hybrid

When hybrid is on, the default `mode` for `recall` is `"hybrid"`. You can
also force one of:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "EmbeddingError",
    "mode": "sparse"
  }
}
```

| Mode | When to reach for it |
|---|---|
| `"dense"` | Pure semantic search. Best when the query is conceptual. |
| `"sparse"` | Pure lexical. Best when the query *is* the answer (a function name, an error string). |
| `"hybrid"` | Default. Use unless you have a reason to constrain. |

## Turning rerank on

```bash
qilin config set rerank_enabled true
qilin down && qilin up
```

What that changes:

- Each `recall` pulls a candidate pool of `rerank_top_k` chunks (default 50).
- The pool is scored by a cross-encoder
  (`Xenova/ms-marco-MiniLM-L-6-v2` by default).
- The top-K *after* reranking is what comes back to the caller.

You can also override per-call:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "where do we parse chunk_size_tokens?",
    "rerank": true,
    "rerank_top_k": 30
  }
}
```

The reranker model downloads to FastEmbed's local cache (`~/.cache/fastembed`)
on first use. Subsequent calls are local-only.

### Picking a reranker model

For most setups, the default is the right balance of quality vs. speed
(~40 ms / candidate on CPU). For higher quality at higher latency:

```bash
qilin config set rerank_model BAAI/bge-reranker-v2-m3
```

The model just needs to exist in the FastEmbed `TextCrossEncoder` catalog -
see the [FastEmbed model list](https://qdrant.github.io/fastembed/examples/Supported_Models/)
for the supported set.

## `recall_files`: "which files are relevant?"

A new MCP tool:

```jsonc
{
  "name": "recall_files",
  "arguments": {
    "query": "vector store sparse-vector handling",
    "top_k": 5
  }
}
```

Returns one entry per source file, ordered by summed score:

```json
[
  {
    "source": "src/qilin/store.py",
    "score": 3.7,
    "top_score": 0.91,
    "hit_count": 4,
    "lines": "120-180",
    "preview": "async def upsert_chunks(...) ...",
    "language": "python"
  },
  ...
]
```

Why use it instead of `recall`?

- LLM token cost is way lower (one preview per file, not per chunk).
- The summed score implicitly de-duplicates: a file with three matching
  chunks beats a file with one strong chunk, which is usually the right
  intuition.
- Pairs naturally with `rerank=true` since the rerank pool is large enough
  that the file ranking is meaningful.

## Cost notes

- **Sparse embedding (ingest):** ~5 ms per chunk on CPU. Negligible.
- **Hybrid query:** one extra round-trip to Qdrant for the sparse prefetch.
  Fused server-side via Qdrant's `Fusion.RRF`, so the client gets one merged
  list back.
- **Rerank:** ~40-80 ms per candidate on CPU (varies by model and CPU). For
  `rerank_top_k=50` that's a single-digit-percent latency hit on the overall
  recall.
- **Model downloads:** sparse model ~3 MB (BM25 is just a tokenizer), rerank
  model ~80 MB. Downloads happen on first use; cached afterwards.

## Caveats

- **Hybrid mode requires a freshly-created collection** that was bootstrapped
  with `hybrid_enabled=true`. Old collections silently degrade to dense
  search even when `mode="hybrid"` is passed.
- **Sparse vectors are not currently filterable**, only fusable. Filters
  (`filter={"language": "rust"}`) still apply to both dense and sparse
  prefetches, but you can't say "match X tokens AND Y other tokens" in a
  single sparse query.
- **The reranker isn't symmetric.** Scores from one model aren't comparable
  to scores from another, so don't compare across configs.
- **First-call latency for the reranker is dominated by the ONNX model
  load.** Subsequent calls within the same process are fast.
