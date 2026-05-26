# Qilin

<p align="center">
  <img src="img/title.jpg" alt="qi lin logo" width="200">
</p>

<p align="center">
  <a href="https://github.com/dev-creations/qilin/actions/workflows/tests.yml"><img src="https://github.com/dev-creations/qilin/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="https://github.com/dev-creations/qilin/actions/workflows/cli-tests.yml"><img src="https://github.com/dev-creations/qilin/actions/workflows/cli-tests.yml/badge.svg" alt="cli tests"></a>
  <a href="https://codecov.io/gh/dev-creations/qilin"><img src="https://codecov.io/gh/dev-creations/qilin/branch/main/graph/badge.svg" alt="coverage"></a>
</p>

Plug and Play memory improvement for your AI using Vector memory, exposed over [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) via Server-Sent Events over HTTPS.

## What is this?

Qilin is a single CLI binary (`qilin`) that bootstraps a complete MCP server on your machine. One `qilin init` and one `qilin up` give you:

- a Qdrant vector store (managed locally, or pointed at your own),
- a TLS-terminated MCP/SSE endpoint your AI clients can connect to,
- a self-signed certificate if you didn't bring your own,
- all configuration neatly tucked into `~/.qilin/`.

No cloning, no editing `.env` files, no hand-rolling docker-compose.

In-depth docs (architecture, migration notes, copy-pasteable recipes) live
under [`docs/`](docs/README.md). The [`CHANGELOG`](CHANGELOG.md) tracks
notable changes per release.

## Prerequisites

1. **Docker** + Docker Compose v2 (Docker Desktop or `docker` + `docker compose` plugin).
2. **Ollama** running on the host, with the embedding model pulled:

   ```bash
   ollama pull nomic-embed-text-v2-moe
   ```

   On Linux, make sure Ollama listens on all interfaces so the qilin container can reach it:

   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```

   On macOS / Windows Docker Desktop, `host.docker.internal` resolves automatically.

## Install the CLI

### macOS / Linux

using brew:

```bash
brew tap dev-creations/qilin
brew install --cask qilin
```

Or manually download using curl.

```bash
curl -fsSL https://raw.githubusercontent.com/dev-creations/qilin/main/scripts/install.sh | sh
```

The installer detects your OS/arch, downloads the matching release binary from GitHub, verifies its SHA-256 against `checksums.txt`, and drops `qilin` into `/usr/local/bin` (or `~/.local/bin` without sudo).

### Windows (PowerShell)

using scoop:

```bash
scoop bucket add qilin https://github.com/dev-creations/scoop-qilin
scoop install qilin
```

Or manually download using powershell.

```powershell
irm https://raw.githubusercontent.com/dev-creations/qilin/main/scripts/install.ps1 | iex
```

Installs `qilin.exe` into `%LOCALAPPDATA%\Programs\qilin` and adds it to your user PATH.

### Build from source

```bash
git clone https://github.com/dev-creations/qilin.git
cd qilin/cli
go build -o qilin ./cmd/qilin
sudo mv qilin /usr/local/bin/
```

Brew tap (`brew install dev-creations/qilin/qilin`) and Scoop bucket support are wired into the release pipeline; they will be enabled once the tap/bucket repos exist.

#### macOS: "Apple cannot verify..." dialog

The release pipeline ad-hoc signs the macOS binaries with [`rcodesign`](https://github.com/indygreg/apple-platform-rs), and both the Homebrew cask and the `install.sh` script strip the `com.apple.quarantine` xattr after install, so a normal `brew install --cask qilin` or `curl ... | sh` should let `qilin --version` run cleanly.

If you downloaded a release tarball manually (or hit the Gatekeeper dialog anyway because of a macOS update), clear the quarantine attribute yourself:

```bash
xattr -dr com.apple.quarantine "$(command -v qilin)"
```

The binary is not yet signed with an Apple Developer ID, so notarization-style Gatekeeper checks won't pass silently for manually-downloaded archives. The `xattr` workaround is the same one [recommended upstream by GoReleaser](https://goreleaser.com/customization/homebrew_casks/#signing-and-notarizing) for unsigned distributions.

## Quick start

```bash
qilin init        # interactive wizard; writes ~/.qilin/{config.json, compose.yaml, certs/}
qilin up          # starts the qilin-mcp container (and Qdrant in managed mode)
qilin status      # confirms everything is running
```

That's it. The SSE endpoint is live at `https://localhost:8443/sse` (TLS) and, by default, `http://localhost:8080/sse` (plain HTTP, loopback only).

For CI or scripted setup, pass flags and skip the prompts:

```bash
qilin init \
  --non-interactive \
  --qdrant-url https://qdrant.example.com:6333 \
  --qdrant-api-key "$QDRANT_KEY" \
  --collection my-team-memory
```

Every wizard field has a matching flag — see `qilin init --help`.

## Connecting an MCP client

Most MCP-aware clients accept an SSE endpoint. Drop this into your client's config (`~/.cursor/mcp.json`, Claude Desktop's config, etc.):

```json
{
  "mcpServers": {
    "qilin": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

| Endpoint | Use when |
|---|---|
| `https://localhost:8443/sse` | The client trusts the self-signed cert, or you've imported it into the OS root store. Use this for any remote / cross-machine access. |
| `http://localhost:8080/sse` | The client is on the same machine and you don't want to deal with cert trust. The HTTP port is bound to `127.0.0.1` on the host, so it's not exposed to the LAN. |

Set `qilin config set server.http_enabled false` (or pass `--no-http` to `qilin init`) to disable the plain endpoint entirely.

### Trusting the self-signed cert

`qilin init` drops a self-signed cert at `~/.qilin/certs/cert.pem` (10-year validity, SAN list covers `localhost` and `127.0.0.1`). To trust it system-wide:

- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.qilin/certs/cert.pem`
- **Linux:** `sudo cp ~/.qilin/certs/cert.pem /usr/local/share/ca-certificates/qilin-ca.crt && sudo update-ca-certificates`
- **Windows:** `Import-Certificate -FilePath $env:USERPROFILE\.qilin\certs\cert.pem -CertStoreLocation Cert:\LocalMachine\Root`
- **Per-app:** point your MCP client's TLS CA bundle at the cert path.

To rotate the cert: `qilin cert regenerate` (then `qilin down && qilin up`).

## CLI reference

| Command | Purpose |
|---|---|
| `qilin init` | Interactive wizard; writes config, certs, and `compose.yaml`. |
| `qilin up` | `docker compose up` for the qilin project. |
| `qilin down [--volumes]` | Stop the stack; `--volumes` wipes stored vectors. |
| `qilin status` | `docker compose ps` for the qilin project. |
| `qilin logs [-f] [service]` | Stream container logs (`qilin-mcp` or `qdrant`). |
| `qilin doctor` | Probe Docker, Ollama, Qdrant, and the TLS cert. |
| `qilin config show \| path \| set <key> <value> \| edit` | Inspect or modify `config.json`. Mutations rewrite `.env` and `compose.yaml` automatically. |
| `qilin cert show \| path \| regenerate` | Inspect or rotate the local TLS cert. |
| `qilin ingest <path> [args...]` | Bulk-ingest a directory by bind-mounting it into the container and running the in-container Python CLI. |
| `qilin recall <query> [args...]` | Run a similarity search via the in-container Python CLI. |
| `qilin version` | Print version, commit, and build platform. |

## Configuration

`~/.qilin/config.json` is the source of truth. The CLI translates it into the environment variables the Python server already understands (Pydantic-settings), so power users can override anything by editing the file or running `qilin config set <dotted.key> <value>`.

| Key | Default | Purpose |
|---|---|---|
| `image` | `ghcr.io/dev-creations/qilin-mcp:<cli-version>` | Docker image to run. |
| `default_collection` | `memory` | Collection used when callers omit one. |
| `qdrant.managed` | `true` | Run a Qdrant container alongside qilin-mcp. |
| `qdrant.url` | _(empty)_ | External Qdrant URL; empty means managed mode. |
| `qdrant.api_key` | _(empty)_ | Optional Qdrant API key. |
| `qdrant.image_tag` | `v1.18.0` | Tag pulled in managed mode. |
| `ollama.url` | `http://host.docker.internal:11434` | Embedding backend. |
| `ollama.embedding_model` | `nomic-embed-text-v2-moe` | Ollama model name. |
| `ollama.embedding_dim` | `768` | Must match the model's output dim. |
| `server.host` | `127.0.0.1` | Host interface to bind. |
| `server.port` | `8443` | TLS listener port. |
| `server.http_enabled` | `true` | Bind a loopback-only plain-HTTP listener. |
| `server.http_port` | `8080` | Plain-HTTP port. |
| `tls.cert_file` / `tls.key_file` | `~/.qilin/certs/{cert,key}.pem` | TLS material. |
| `tls.self_signed` | `true` | Set by `qilin init`; `qilin cert regenerate` refuses to overwrite user-provided certs. |
| `chunking.size_tokens` | `450` | Target tokens per chunk. |
| `chunking.overlap_tokens` | `50` | Overlap between consecutive chunks. |
| `chunking.batch_size` | `16` | Batch size for Ollama `/api/embed`. |
| `auth_token` | _(unset)_ | Bearer token (or list) required on incoming MCP requests. Unset = open to localhost. See [expose-on-lan](docs/recipes/expose-on-lan.md). |
| `streamable_http_enabled` | `true` | Mount FastMCP's streamable HTTP app at `/mcp` alongside `/sse`. |
| `ttl_sweep_seconds` | `300` | How often the TTL sweeper deletes expired chunks. |
| `recall_log_path` | `~/.qilin/logs/recall.jsonl` | JSONL recall log destination. Empty string disables logging. |
| `collections.<name>.chunk_size_tokens` | _(inherits)_ | Per-collection chunk size override. |
| `collections.<name>.chunk_overlap_tokens` | _(inherits)_ | Per-collection chunk overlap override. |
| `collections.<name>.ttl_seconds` | _(unset)_ | If set, chunks in this collection are auto-deleted that many seconds after `remember`. See [scratch-vs-knowledge-collections](docs/recipes/scratch-vs-knowledge-collections.md). |

Override the config directory with `--qilin-home <dir>` or `$QILIN_HOME`. On Linux, `$XDG_CONFIG_HOME/qilin` is also honored.

## What's included?

- Embeddings: [`nomic-embed-text-v2-moe`](https://ollama.com/library/nomic-embed-text-v2-moe) served by the host's Ollama (768-dim, multilingual MoE, 512-token context).
- Vector store: [Qdrant](https://qdrant.tech/) (cosine distance).
- Transport: MCP over SSE on `https://<host>:8443/sse` with auto-generated self-signed TLS.
- Chunking: automatic, token-aware (defaults to ~450-token windows with 50-token overlap), so dropping a 10k-word document in is fine.

## Architecture

```
MCP Client (any AI / IDE)
        |
        | HTTPS, SSE  (https://localhost:8443/sse)
        v
qilin-mcp container  ----embed---->  Host Ollama
        |                            (nomic-embed-text-v2-moe @ :11434)
        | HTTP
        v
qdrant container  (vectors + payloads, persistent volume)
```

Two containers (`qilin-mcp`, `qdrant`) + one host process (Ollama) when running in managed mode. The Go `qilin` CLI on the host generates and owns the compose file but doesn't otherwise run between requests.

## Inspecting the vector store

Qdrant ships a full web dashboard inside the same container. It's reachable at:

**<http://localhost:6333/dashboard>**

The dashboard lets you browse collections, inspect points and payloads, run ad-hoc similarity searches, and — most usefully — see a 2D projection of your embeddings under the **Visualize** tab (colorable by any payload field, e.g. `language` or `repo`).

The port is bound to `127.0.0.1` only, so it isn't reachable from the LAN.

## Tools exposed over MCP

| Tool | Purpose |
|---|---|
| `remember(text, collection?, metadata?, source?, language?)` | Chunks, embeds, and stores text. Returns `{collection, chunks_written, ids, document_hash}`. Idempotent per `(source, content)`. |
| `recall(query, collection?, top_k?, filter?, score_threshold?, context_window?, group_by_source?, mmr_lambda?, mode?, rerank?, rerank_top_k?)` | Vector search; returns a flat list of hits (see below). |
| `recall_files(query, ...)` | Groups recall hits by `source`, returns one entry per file (see [hybrid-search](docs/recipes/hybrid-search.md)). |
| `forget(ids?, collection?, filter?)` | Deletes points by id or by payload filter. |
| `list_collections()` | Lists collection names. |
| `create_collection(name)` | Creates an empty collection (idempotent). |
| `stats(collection?)` | Returns counts/status for a collection. |
| `mark_useful(id, useful?, collection?)` | Thumbs-up/down a recall hit; boosts future recall scores for that chunk. See [recall-feedback-loop](docs/recipes/recall-feedback-loop.md). |

### `recall` response shape

Since v1.0.0 each hit is a flat dict:

```json
{
  "id": "abc-123",
  "score": 0.83,
  "text": "...",
  "source": "src/foo.py",
  "language": "python",
  "start_line": 30,
  "end_line": 95,
  "lines": "30-95",
  "chunk_ordinal": 2,
  "chunk_count": 5,
  "document_hash": "...",
  "created_at": "2025-12-30T10:00:00Z",
  "extra_metadata": { "label": "weekly" }
}
```

If you're coming from 0.x, the [migration guide](docs/migrating-to-1.0.md) has
the full delta.

### Recall tuning

`recall` takes three optional post-processing knobs:

- `context_window=N` - merge each hit with its `N` neighbor chunks (same
  `source`), so a function split across a chunk boundary still comes back as
  one contiguous block.
- `group_by_source=True` - keep at most one hit per `source`.
- `mmr_lambda=0..1` - re-rank candidates by Maximal Marginal Relevance for
  diversity.

See [Recipe: Tuning recall](docs/recipes/tuning-recall.md) for worked examples.

### Code-aware chunking

When the caller supplies a `language` (or when `qilin ingest` detects one
from a file extension), Qilin runs a tree-sitter parser instead of the prose
chunker and emits one chunk per function/class/method. Code chunks also
carry `defines`, `imports`, `signature`, and `language` payload fields, with
a KEYWORD index on `defines` and `language` so `filter={"defines": "MyClass"}`
or `filter={"language": "rust"}` stays fast. Supported languages: Python, Go,
JavaScript, TypeScript, TSX, Rust. Other languages fall back to the prose
chunker. See [Recipe: Code-aware search](docs/recipes/code-search.md).

### Keeping memory in sync (incremental ingest + watch mode)

`qilin ingest` is incremental: it scans the destination collection on every
run, re-embeds files whose content changed (cleaning up the old chunks in the
same pass), and skips files whose hash still matches. Pass `--prune` to also
forget sources that no longer exist on disk (scoped to the active
`--source-prefix`). For editor-loop workflows, `qilin watch <path>` listens
for filesystem events and re-ingests on save. See
[Incremental ingest](docs/recipes/incremental-ingest.md) and
[Watch mode](docs/recipes/watch-mode.md).

### Per-collection chunking

The settings block accepts a `collections: {name: {...}}` map so different
collections can have different `chunk_size_tokens` / `chunk_overlap_tokens`.
Useful for splitting code (fine-grained, small chunks) from docs (larger
chunks). See
[Code vs. docs collections](docs/recipes/code-vs-docs-collections.md).

### Hybrid search and reranking

Two opt-in retrieval upgrades:

- `hybrid_enabled=true` — new collections store dense + BM25 sparse vectors;
  `recall(mode="hybrid")` fuses them via Qdrant's server-side Reciprocal
  Rank Fusion.
- `rerank_enabled=true` (or per-call `rerank=true`) — a FastEmbed
  cross-encoder reorders the candidate pool before truncating to `top_k`.

A new MCP tool, `recall_files`, returns the top-K *files* (with previews and
line spans) rather than chunks. See
[Recipe: Hybrid search and reranking](docs/recipes/hybrid-search.md).

### Chunking behaviour

Long inputs are automatically split before embedding:

- Paragraphs (`\n\n`) → sentences → token windows.
- Default window: **450 tokens** with **50-token overlap** (tunable via `chunking.size_tokens` / `chunking.overlap_tokens` in the config).
- Stays safely below the embedder's 512-token context window.
- A 10,000-word document yields roughly 30 chunks; embeddings are produced in batches (`chunking.batch_size`, default 16) against Ollama's `/api/embed`.
- Each chunk's payload includes `text`, `chunk_ordinal`, `chunk_count`, `document_hash`, `created_at`, `source`, and any caller-provided `metadata`, so `recall` results carry enough context to be useful standalone.

### Idempotency

Point IDs are derived as `uuid5(POINT_NS, f"{source}::{sha256(text)}::{ordinal}")`. Re-running `remember` with the same `text` and `source` overwrites existing points instead of duplicating them.

## Ingesting an existing repository

```bash
qilin ingest /path/to/your/repo --collection myrepo
```

The host CLI bind-mounts the directory into the running container and forwards the rest of the flags to the Python CLI. See `qilin ingest --help` (with the stack running) for the full flag list — includes `--include`, `--exclude`, `--label`, `--git-sha`, `--dry-run`, etc.

Default behaviour:

- **Included extensions:** common code/config/docs extensions (`.py`, `.ts`, `.go`, `.rs`, `.md`, `.toml`, `.yaml`, ...). Override with `--include .proto --include .graphql`.
- **Excluded directories:** `.git`, `.venv`, `node_modules`, `__pycache__`, `dist`, `build`, etc. Add more with `--exclude infra --exclude vendor`.
- **Excluded files:** common lockfiles (`package-lock.json`, `poetry.lock`, `Cargo.lock`, ...).
- **`.gitignore`** at the repo root is honored unless `--no-respect-gitignore`.
- **Size limit:** files larger than 256KB are skipped (`--max-bytes`).

### Caveat: stale chunks

`qilin ingest` is idempotent for *unchanged* files (skipped) and *modified* files where the new chunk count >= the old chunk count (overwritten). If you modify a file so that the *new* chunked output has **fewer** chunks than before, the surplus old chunks linger in the collection (their deterministic IDs include `chunk_ordinal` and won't be overwritten). To force a clean ingest of a single source, use the MCP `forget` tool first, or recreate the collection.

## Advanced: run from source (no CLI)

If you'd rather skip the CLI and orchestrate things yourself, the docker-compose file in this repo still works as before:

```bash
git clone https://github.com/dev-creations/qilin.git
cd qilin
cp .env.example .env
docker compose build
docker compose up -d
```

The first start auto-generates a self-signed cert stored in the `qilin_certs` named volume; subsequent starts reuse it.

Local (non-Docker) run:

```bash
pip install -e .
uvicorn qilin.server:app --host 0.0.0.0 --port 8443
```

(TLS optional when running outside the container.)

## Development

### CLI (Go)

```bash
cd cli
go test ./...           # unit tests
go build -o qilin ./cmd/qilin
```

The release pipeline is GoReleaser-driven; run `goreleaser release --snapshot --clean --skip=publish` for a local dry-run that produces every platform's archive under `cli/dist/`.

### Server (Python)

```bash
pip install -e ".[dev]"
ruff check .
pytest --cov --cov-report=term-missing
```

The suite uses mocked Qdrant and Ollama clients, so it runs without any external services. Coverage is enforced at **80%** via `[tool.coverage.report] fail_under = 80` in [`pyproject.toml`](pyproject.toml).

### Continuous integration

- [`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs `ruff check` followed by `pytest` (with coverage) on Python 3.11 and 3.12.
- [`.github/workflows/cli-tests.yml`](.github/workflows/cli-tests.yml) runs `go vet` and `go test -race` on Linux, macOS, and Windows.
- [`.github/workflows/release.yml`](.github/workflows/release.yml) fires on `v*` tags: GoReleaser builds the CLI binaries, and Docker Buildx pushes a multi-arch `ghcr.io/dev-creations/qilin-mcp` image.

## Production deployment

For anything beyond localhost development, see:

- **[Exposing Qilin on the LAN](docs/recipes/expose-on-lan.md)** — TLS trust,
  bearer-token auth (`auth_token` setting), streamable HTTP transport
  (`/mcp`), and firewall notes.
- **[Scratch vs. knowledge collections](docs/recipes/scratch-vs-knowledge-collections.md)** —
  TTL-backed session memory beside long-lived knowledge.
- **[Recall feedback loop](docs/recipes/recall-feedback-loop.md)** — recall
  analytics (`qilin recall-log`) plus `mark_useful` to bias future
  retrieval.

### Out of scope (for now)

- Multi-tenant isolation beyond per-collection separation. Bearer-token
  auth is single-tier; if you need per-team scopes, put Qilin behind an
  auth proxy.
- Native (non-Docker) host runtime under the Go CLI — Docker is currently
  required.

## License

MIT.
