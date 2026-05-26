# Recipe: Watch mode

**Goal:** keep a Qilin collection in sync with an editor session. On save,
the file gets re-embedded; on delete, its chunks get forgotten.

## TL;DR

```bash
qilin watch /path/to/repo --collection myrepo
```

Qilin listens to filesystem events with [`watchfiles`](https://github.com/samuelcolvin/watchfiles),
coalesces them over a 500 ms window, applies the same gitignore /
extension / max-size filters as `qilin ingest`, and runs the per-file ingest
path for each change. Press Ctrl+C to stop.

## What it watches

The watcher uses the same filter set as `qilin ingest`:

- `.gitignore` at the repo root is honored.
- Default include extensions (`.py`, `.md`, `.go`, `.ts`, ...).
- Files larger than `--max-bytes` are ignored.
- Common build directories and lockfiles are skipped.

So a typical save-in-Cursor → Qilin-re-embeds pipeline only fires for files
that would have been included in a normal `qilin ingest` of the same path.

## Tuning the debounce

```bash
qilin watch /path/to/repo --debounce-ms 200
```

| Value | Trade-off |
|---|---|
| `100` | Snappiest, but high CPU on big editor saves (formatters that touch many files). |
| `500` (default) | Sweet spot for single-file edits. |
| `2000` | Useful when you're rebasing or applying a patch series and want batches. |

`watchfiles` deduplicates events within the window, so a save that triggers
multiple `modified` events (some editors do this) still only re-ingests
once.

## What happens on save vs. delete

| Event | Action |
|---|---|
| `added` | Treated like a new file. Embedded and stored. |
| `modified` | Re-ingested; the chunks at the old content hash are deleted. |
| `deleted` (file gone from disk) | Forgotten via `forget(filter={"source": ...})`. |
| Renamed | One delete + one new file. Old chunks go, new chunks come in. |

Each event is logged to the console so you can watch the sync stream:

```
+ src/foo.py  (3 chunks)
+ src/bar.py  (1 chunks)
- src/old.py
```

## Per-collection settings still apply

Watch uses `tools.remember` under the hood, which threads
[per-collection config](code-vs-docs-collections.md) through chunking. So a
watch on a collection with a custom `chunk_size_tokens` chunks accordingly.

## Use cases

- **Persistent memory of your current branch.** Watch your repo into a
  `scratch` collection while you work; pair with [scratch collections](scratch-vs-knowledge-collections.md)
  for automatic expiry.
- **Live docs for a tool.** Watch your `docs/` directory into a collection
  the AI client always queries.
- **Cross-project shared workspace.** Watch a `notes/` directory you keep
  outside of any git repo.

## Tearing it down

`qilin watch` is a foreground process; Ctrl+C exits cleanly and tears down
the embedder / store connections. There's no daemon to manage. If you want
it to keep running in the background, use your shell's job control or run
under a process supervisor like `tmux` / `systemd --user`.

## Caveats

- **No retroactive prune.** If you started watch *after* deleting a file,
  Qilin won't know about the delete. Run `qilin ingest --prune` once at the
  start of a watch session if you care about catching pre-existing orphans.
- **High-frequency change storms** (e.g. running a code formatter on the
  whole repo) trigger a re-embed per touched file. Either raise
  `--debounce-ms` or run the formatter, then start `qilin watch`
  afterwards.
- **Symlinks** are followed by default; watchfiles respects the OS's notion
  of "same inode" so cycle detection is implicit.
