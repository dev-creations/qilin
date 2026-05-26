# Recipe: Close the loop with `mark_useful` and `qilin recall-log`

Plain vector recall is a guess. This recipe wires up two small features so
Qilin gets *better at your questions* over time:

- Every `recall` writes a structured JSONL event to a log file.
- The new `mark_useful` MCP tool lets you (or your agent) thumbs-up/down
  individual hits.
- The next `recall` applies a small score boost proportional to net votes.

## 1. Make sure logging is on

The default log path is `~/.qilin/logs/recall.jsonl`. To override or to
disable:

```json
{
  "recall_log_path": "/var/log/qilin/recall.jsonl"
}
```

```json
{ "recall_log_path": "" }
```

The server appends one line per `recall` call. Each event looks like:

```json
{
  "ts": "2026-05-26T11:02:31+00:00",
  "query": "how do we handle Ollama timeouts",
  "collection": "memory",
  "top_k": 5,
  "mode": "hybrid",
  "rerank": true,
  "latency_ms": 84.3,
  "hits": [
    {"id": "1e6f...", "score": 0.71, "source": "src/qilin/embeddings.py", "lines": "120-180"},
    ...
  ]
}
```

## 2. Inspect what your team has been asking

```bash
qilin recall-log -n 20
qilin recall-log --since 1h -n 50
qilin recall-log --since 30m --path /var/log/qilin/recall.jsonl
```

Output is paginated by event with the timestamp, collection, mode, and the
top three hits per event.

This is your easiest debugging tool when somebody says "Qilin's getting
worse." The two things to look for:

- Same query gets very different top results - feed inconsistency, retry the
  ingest.
- Latency creeps - hybrid + rerank is doing too much work; turn one off in
  `~/.qilin/config.json`.

## 3. Upvote / downvote hits with `mark_useful`

The MCP tool takes the hit id straight from a `recall` response:

```python
hits = await client.call_tool("recall", {"query": "ollama timeouts"})

# the agent (or human) decides this one was actually relevant:
await client.call_tool(
    "mark_useful", {"id": hits[0]["id"], "useful": True}
)

# nope, this one was wrong:
await client.call_tool(
    "mark_useful", {"id": hits[3]["id"], "useful": False}
)
```

Each call increments (or decrements) a `feedback` integer on that chunk's
payload. The score boost in `recall` is:

```
multiplier = clamp(1 + 0.05 * feedback, 1/1.5, 1.5)
```

So:

- +1 vote = +5% score
- +10 votes = +50% (capped)
- -2 votes = ~-10%

The cap protects you from people gaming the system; the per-vote step is
deliberately small so you need multiple humans agreeing for it to dominate.

## 4. CLI workflow

Most teams pair this with a tiny shell wrapper:

```bash
qilin-upvote() {
  curl -s -X POST -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${QILIN_TOKEN}" \
    -d "{\"id\":\"$1\"}" \
    https://qilin.local:8443/mcp/tools/mark_useful
}
```

Then you can copy a hit ID from `qilin recall-log` output and bump it with
`qilin-upvote <id>` straight from your terminal.

## 5. Audit boosting in the log

Boosted recalls show their boosted scores in the JSONL stream (the score
field is the post-boost value). The original score is not stored - if you
need it, run with `rerank: false` and `feedback: 0` for a baseline.

## Limits

- Boost is per chunk, not per source. If you rebuild a source and the
  deterministic IDs change, the votes go with the old chunk IDs.
- The sweeper from
  [scratch-vs-knowledge-collections](scratch-vs-knowledge-collections.md)
  will delete upvoted scratch chunks at TTL. Promote them to `knowledge`
  before they expire.
- `feedback` is a soft signal. Hard pins still go in
  `metadata={"pinned": true}` plus a custom filter.

## See also

- [tuning-recall](tuning-recall.md) - retrieval knobs that pair well with
  feedback (`mmr_lambda`, `context_window`).
- [hybrid-search](hybrid-search.md) - the `mode` field that shows up in the
  recall log.
