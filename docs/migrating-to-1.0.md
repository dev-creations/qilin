# Migrating to Qilin 1.0

Qilin 1.0 changes the shape of the `recall` MCP tool response. Storage,
collections, point IDs and payloads on disk are *unchanged*; only the response
that comes back over MCP is different.

If your client just displays whatever Qilin returns, you do not need to do
anything. If you wrote code that pattern-matches on the previous nested
`metadata` field, read on.

## What changed

### Before (0.x)

```json
{
  "id": "abc-123",
  "score": 0.83,
  "text": "def foo() -> int: ...",
  "metadata": {
    "source": "src/foo.py",
    "language": "python",
    "chunk_ordinal": 2,
    "chunk_count": 5,
    "document_hash": "...",
    "created_at": "2025-12-30T10:00:00Z",
    "label": "weekly",
    "repo": "qilin"
  }
}
```

### After (1.0)

```json
{
  "id": "abc-123",
  "score": 0.83,
  "text": "def foo() -> int: ...",
  "source": "src/foo.py",
  "language": "python",
  "start_line": 30,
  "end_line": 95,
  "lines": "30-95",
  "chunk_ordinal": 2,
  "chunk_count": 5,
  "document_hash": "...",
  "created_at": "2025-12-30T10:00:00Z",
  "extra_metadata": {
    "label": "weekly",
    "repo": "qilin"
  }
}
```

Concretely:

- The `metadata` key is gone.
- First-class fields (`source`, `language`, `start_line`, `end_line`,
  `chunk_ordinal`, `chunk_count`, `document_hash`, `created_at`) are promoted
  to the top level alongside `id`, `score`, `text`.
- A new `lines` string field is added that renders the inclusive line span
  (`"30-95"`, or `"30"` for single-line hits) for citation building.
- Anything caller-supplied via `remember(metadata=...)` (e.g. `label`, `repo`)
  lands under `extra_metadata`.

## Why

Most consumers want to do one of these three things with a hit:

1. Render a citation like `src/foo.py:30-95`.
2. Filter or branch on `source` / `language`.
3. Pass the text plus metadata into an LLM prompt.

The old shape forced a `hit["metadata"]["source"]` indirection for every one
of those. The new shape also surfaces `start_line`/`end_line` for the first
time, which makes click-to-open citations possible without re-tokenizing the
source.

## Upgrade checklist

If you have client code that reads recall results:

- Replace `hit["metadata"]["source"]` with `hit["source"]`.
- Replace `hit["metadata"]["chunk_ordinal"]` with `hit["chunk_ordinal"]`.
- For anything caller-supplied (custom labels, git SHAs, repo names) read from
  `hit.get("extra_metadata", {})` instead of `hit["metadata"]`.
- Optional: surface `hit.get("lines")` next to `hit["source"]` to build
  click-to-open citations.

A defensive pattern that works against both 0.x and 1.x:

```python
def get_source(hit: dict) -> str | None:
    return hit.get("source") or hit.get("metadata", {}).get("source")
```

## What did *not* change

- `remember`, `forget`, `list_collections`, `create_collection`, `stats`
  responses are unchanged.
- Stored payloads inside Qdrant are unchanged - the new fields
  (`start_line`/`end_line`) are *additive* on the storage side and missing for
  pre-1.0 chunks. Hits from old data simply omit those fields rather than
  failing.
- Point IDs and the deterministic ID scheme are unchanged. You do not need to
  re-ingest.

## Re-ingesting to get line spans on old data

Old chunks won't have `start_line`/`end_line`. To backfill, re-run ingest with
`--force`:

```bash
qilin ingest /path/to/repo --collection myrepo --force
```

This overwrites every chunk in place with the new payload fields. The
deterministic ID scheme guarantees you won't end up with duplicates.
