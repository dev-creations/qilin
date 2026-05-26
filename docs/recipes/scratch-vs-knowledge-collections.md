# Recipe: Scratch vs knowledge collections

Not every snippet you put into memory deserves to live forever. The pattern
in this recipe splits Qilin into two collections:

- **`knowledge`** - long-lived, hand-curated context (no TTL).
- **`scratch`** - short-lived session memory that Qilin auto-deletes after a
  configurable TTL.

The benefits: scratch collections don't drown your `recall` results in old
trivia, and you stop hoarding gigabytes of stale chat snippets.

## How TTL works in Qilin

When a collection has `ttl_seconds` set, every chunk written via `remember`
gets an `expires_at` ISO-8601 timestamp added to its payload. A background
sweeper inside the Qilin server runs every `ttl_sweep_seconds` (default 300)
and deletes points whose `expires_at` is in the past.

The sweeper is purely time-based - reading a chunk does **not** reset its
TTL. If you want sliding-window TTL, ingest the chunk again.

## 1. Configure the two collections

Edit `~/.qilin/config.json`:

```json
{
  "default_collection": "knowledge",
  "ttl_sweep_seconds": 60,
  "collections": {
    "knowledge": {},
    "scratch": {
      "ttl_seconds": 86400,
      "chunk_size_tokens": 256
    },
    "session": {
      "ttl_seconds": 3600
    }
  }
}
```

That gives you:

- `knowledge` - permanent.
- `scratch` - drops after 24h, also uses a smaller chunk size since session
  fragments tend to be short.
- `session` - drops after 1h (great for "what was I just working on?").

Restart the server. Startup logs should show no sweeper banner if no TTL
collections exist, and a periodic `TTL sweep: scratch removed=12` once they
do.

## 2. Send chats / quick notes to scratch

```python
await client.call_tool(
    "remember",
    {
        "text": "Failure was in retrieve(); we shimmed it to return [] on 404.",
        "collection": "scratch",
        "source": "chat-2026-05-26",
        "metadata": {"actor": "session"},
    },
)
```

## 3. Promote useful scratch entries to knowledge

When you find a scratch chunk that's worth keeping, mark it useful (so the
feedback loop boosts it - see [recall-feedback-loop](recall-feedback-loop.md))
and then re-ingest the same text into `knowledge`. The deterministic ID
across collections means duplicates are a non-issue.

```python
await client.call_tool(
    "remember",
    {"text": original_chunk_text, "collection": "knowledge", "source": "promoted/<id>"},
)
```

## 4. Confirm the sweeper is doing its job

```bash
docker exec -it qilin qilin recall-log --since 1h -n 100
```

Watch the `collection: scratch` events shrink as the sweeper trims old
entries.

Or query Qdrant directly:

```bash
curl -s -k https://localhost:8443/healthz
curl -s "http://qdrant:6333/collections/scratch" | jq '.result.points_count'
```

## 5. Force-flush a scratch collection

If you want to wipe the scratch collection between sessions instead of
waiting for the sweeper:

```python
await client.call_tool("forget", {"filter": {"actor": "session"}, "collection": "scratch"})
```

Or from the CLI host:

```bash
curl -X DELETE "http://qdrant:6333/collections/scratch"
```

(Recreate it with `qilin ingest <path> --collection scratch` or by calling
`create_collection` next time.)

## Tuning notes

- `ttl_sweep_seconds` lower than 60 makes Qdrant work harder for nothing.
- The sweeper uses Qdrant's `DatetimeRange(lt=now)` filter so it scales to
  millions of points without scanning each one.
- The sweeper survives transient Qdrant errors - it logs and tries again
  on the next tick.

## See also

- [incremental-ingest](incremental-ingest.md) - the durable counterpart for
  `knowledge` collections.
- [recall-feedback-loop](recall-feedback-loop.md) - how `mark_useful` plays
  with this pattern.
