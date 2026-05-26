# Recipe: Incremental ingest

**Goal:** keep a Qilin collection in sync with a directory on disk - new
files added, modified files re-embedded, deleted files forgotten - without
wiping and re-ingesting from scratch.

## How `qilin ingest` decides what to do

On every run, Qilin scans the destination collection (via Qdrant `scroll`)
and builds an in-memory map of every source it has already stored, with its
content hash and chunk count. For each on-disk file:

| Status | What happens |
|---|---|
| New file (no prior `source`) | Embedded and stored. |
| Same `source`, same `sha256(content)` | Skipped. |
| Same `source`, different content | New chunks ingested. Old chunks (with the old hash) are deleted in the same pass. |
| Caller forced (`--force`) | Re-ingested even when the hash matches. |

This is the default behavior in 1.x onwards. No flag needed. There is no
manifest file on disk; the collection itself is the source of truth.

## Cleaning up deleted files (`--prune`)

By default, files removed from disk leave their chunks behind. To also
forget those:

```bash
qilin ingest /path/to/repo --collection myrepo --prune
```

`--prune` deletes every source that:

- exists in the collection,
- does *not* show up in the current ingest walk,
- *and* shares the current `--source-prefix` (so a prune of `--source-prefix myrepo/`
  cannot accidentally wipe sources under `other-repo/`).

If you don't pass `--source-prefix`, prune considers every source in the
collection in scope.

## Forcing a clean re-ingest

When you need a fresh state (after changing the embedding model, for
example):

```bash
qilin ingest /path/to/repo --collection myrepo --force --prune
```

`--force` overrides the hash-match skip, so every file gets re-embedded;
`--prune` cleans up sources that have moved on disk. Together they
effectively reset the collection without you having to delete it manually.

## Cost notes

- The initial `scan_sources` call paginates through the collection at 256
  payloads per round-trip. For ~100k chunks that's ~400 calls - measure
  in tenths of a second over loopback.
- Stale-hash cleanup uses a payload filter delete, which is constant-time
  in Qdrant regardless of how many points match.
- `--prune` issues one delete per orphaned source. If you have hundreds of
  orphans, prune is the slowest part of an incremental ingest.

## Inspecting state from the CLI

```bash
qilin stats --collection myrepo
```

shows the total point count. For a per-source breakdown drop into a Python
REPL inside the container:

```python
from qilin.store import get_store
import asyncio

async def show():
    s = await get_store()
    sources = await s.scan_sources("myrepo")
    for src, entries in sorted(sources.items()):
        for e in entries:
            print(f"  {src}  hash={e['document_hash'][:8]} count={e['chunk_count']}")

asyncio.run(show())
```

If a source shows up with more than one entry that's a sign of an
interrupted incremental ingest run - rerun with `--prune` or do a `--force`
re-ingest of that file.

## Pairing with `qilin watch`

For an editor-loop workflow, use [Watch mode](watch-mode.md). It calls the
same per-file path as `ingest`, so the same orphan-cleanup rules apply.

## Caveats

- **Multiple ingest roots into one collection:** if you run two `ingest`
  invocations into the same collection without distinct `--source-prefix`
  values, `--prune` from the second run will delete sources from the first.
  Always use prefixes when sharing a collection.
- **External writes** (e.g. an MCP client calling `remember` directly):
  Qilin won't track those for pruning purposes. They'll show up in
  `scan_sources` and be subject to `--prune` if their source doesn't match
  any walked file.
- **Renamed files** look like one delete + one new file to incremental
  ingest. Without `--prune` the old chunks stay; with `--prune` they go.
