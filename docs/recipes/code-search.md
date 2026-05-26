# Recipe: Code-aware search

**Goal:** index a code repository so that `recall` returns syntactically clean
chunks (whole functions / classes) and exposes symbol filters like
`filter={"defines": "MyClass"}`.

## How it works

When `remember(text, language=...)` is called with one of the supported
languages, Qilin runs a tree-sitter parser instead of the prose chunker. Each
top-level definition (function, class, method, type) becomes one atomic unit;
small adjacent definitions pack into a single chunk; oversized classes get
split into their individual methods.

Every code chunk's Qdrant payload picks up three new fields:

- `defines` - a list of qualified symbol names declared in the chunk
  (`["greet", "Counter", "Counter.increment", "Counter.reset"]`).
- `imports` - the import statements at the top of the file, copied onto
  *every* chunk so the LLM can use them as context without a second recall.
- `signature` - the first non-empty line of the first definition in the
  chunk, useful as a hover/preview.

Plus the existing `language` is promoted to a top-level payload field, with
a KEYWORD index, so language-scoped queries are cheap.

## Supported languages

| Language hint | Tree-sitter grammar | What gets chunked |
|---|---|---|
| `python` | `python` | `def`, `class`, methods. Classes too big to fit are split into methods. |
| `go` | `go` | `func`, `type ... struct/interface`, methods. |
| `javascript` | `javascript` | `function`, `class`, methods, `export`-wrapped variants. |
| `typescript` | `typescript` | Same as JS plus `interface`, `type`, `enum`. |
| `tsx` | `tsx` | Same as TS. |
| `rust` | `rust` | `fn`, `struct`, `enum`, `trait`, `impl`, `mod`. |

Unsupported languages fall back to the prose chunker - they still get stored,
they just don't get `defines`/`imports`/`signature` metadata.

## Ingesting a repo

`qilin ingest` auto-detects the language from the file extension and passes
it to `remember`. So this is the entire setup:

```bash
qilin ingest /path/to/my-repo --collection myrepo
```

Files with recognized extensions go through the code-aware path; everything
else (markdown, config, plain text) goes through the prose chunker.

## Querying by symbol

Symbol filters use the standard `recall` filter syntax. Single value:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "how does Counter.reset reach zero?",
    "collection": "myrepo",
    "filter": { "defines": "Counter.reset" }
  }
}
```

Match-any (list value) lets you scope to several symbols:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "increment vs decrement semantics",
    "collection": "myrepo",
    "filter": { "defines": ["Counter.increment", "Counter.decrement"] }
  }
}
```

Both filters use the KEYWORD payload index added in 1.1, so they stay fast
on large collections.

## Scoping queries to a language

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "error handling pattern",
    "filter": { "language": "rust" }
  }
}
```

Useful in mixed-language repos where you know the answer lives in a
particular file family.

## Combining with neighbor expansion

Code chunks are *already* whole functions in most cases, so `context_window`
gets less use than with prose. The exception: classes split into methods,
where pulling in one neighbor on each side reconstructs the class:

```jsonc
{
  "name": "recall",
  "arguments": {
    "query": "Counter behavior",
    "filter": { "defines": "Counter.increment" },
    "context_window": 2
  }
}
```

## Caveats

- **Tree-sitter is a heuristic**, not a type checker. Symbols defined inside
  conditionals (`if cond: def foo(): ...`) or via metaclasses won't show up.
- **`defines` is per-chunk, not per-file.** A class split into methods has
  the class name *only* on the chunk that contains the class header; the
  per-method chunks list only their qualified name.
- **Imports are duplicated** on every chunk. For a 100-import file this
  means a few KB of redundancy per chunk; that's fine for retrieval-time
  context but bear it in mind if you're inspecting raw payloads.
- **`tree-sitter` native wheels** are required. Qilin ships them via
  `tree-sitter-language-pack`. If a wheel is missing on your platform Qilin
  logs a warning and falls back to prose chunking automatically.

## Backfilling old data

Pre-1.1 chunks don't have symbol metadata. To backfill, re-run ingest with
`--force`:

```bash
qilin ingest /path/to/my-repo --collection myrepo --force
```

The deterministic-ID scheme overwrites old chunks in place; you won't end up
with duplicates.
