# Qilin

Vector memory for any AI, exposed over the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP)
via Server-Sent Events over HTTPS.

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

Two containers (`qilin-mcp`, `qdrant`) + one host process (Ollama). The Ollama server stays on the host so it can use your GPU; the container reaches it via `host.docker.internal`.

## Prerequisites

1. Docker + Docker Compose v2.
2. Ollama installed and running on the host, with the embedding model pulled:

   ```bash
   ollama pull nomic-embed-text-v2-moe
   ```

   Make sure Ollama listens on all interfaces so the container can reach it:

   ```bash
   # Linux: edit the systemd unit, or run with
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```

   On macOS / Windows Docker Desktop, `host.docker.internal` resolves automatically; on Linux the compose file already wires `host.docker.internal:host-gateway` for you.

## Quick start

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

The server is now reachable at `https://localhost:8443/sse`. The first start auto-generates a self-signed certificate stored in the `qilin_certs` named volume; subsequent starts reuse it.

Sanity check:

```bash
curl -k https://localhost:8443/healthz
# -> {"ok": true, "qdrant": "ok", "embedder": "ok", ...}
```

## Inspecting the vector store

Qdrant ships a full web dashboard inside the same container. It's reachable at:

**<http://localhost:6333/dashboard>**

The dashboard lets you browse collections, inspect points and payloads, run ad-hoc similarity searches, and — most usefully — see a 2D projection of your embeddings under the **Visualize** tab (colorable by any payload field, e.g. `language` or `repo`).

The port is bound to `127.0.0.1` only, so it is not reachable from the LAN. Remove the `ports:` block under the `qdrant` service in [docker-compose.yml](docker-compose.yml) to disable it entirely.

## Connecting an MCP client

The container exposes the MCP/SSE endpoint on two ports:

| Endpoint | Use when |
|---|---|
| `https://localhost:8443/sse` | The client trusts the self-signed cert (or you've imported it into the OS root store). Use this for any remote / cross-machine access. |
| `http://localhost:8080/sse` | The client is on the same machine and you don't want to deal with cert trust. The HTTP port is bound to `127.0.0.1` on the host, so it is **not** exposed to the LAN. |

Set `MCP_HTTP_ENABLED=0` (or remove the `127.0.0.1:8080:8080` line from compose) to disable the plain endpoint entirely.

Most MCP-aware clients accept an SSE endpoint:

```json
{
  "mcpServers": {
    "qilin": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

For Cursor specifically, drop that snippet into `%USERPROFILE%\.cursor\mcp.json` (global) or `.cursor/mcp.json` (per-project) and fully restart Cursor. Cursor's SSE client uses Chromium's `fetch` (not Node's), so it reads the OS certificate store rather than `NODE_EXTRA_CA_CERTS` — which is why the plain-HTTP endpoint is the easiest path for local Cursor use.

### Trusting the self-signed certificate

Extract the cert from the running container:

```bash
docker compose cp qilin-mcp:/certs/cert.pem ./qilin-ca.pem
```

Then:

- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain qilin-ca.pem`
- **Linux:** copy to `/usr/local/share/ca-certificates/qilin-ca.crt` and run `sudo update-ca-certificates`.
- **Windows:** `Import-Certificate -FilePath qilin-ca.pem -CertStoreLocation Cert:\LocalMachine\Root`
- **Per-app:** point your MCP client's TLS CA bundle at `qilin-ca.pem`.

Browsers and most HTTP clients also expose a "trust on first use" prompt if you visit `https://localhost:8443/` once.

## Tools exposed over MCP

| Tool | Purpose |
|---|---|
| `remember(text, collection?, metadata?, source?)` | Chunks, embeds, and stores text. Returns `{collection, chunks_written, ids, document_hash}`. Idempotent per `(source, content)`. |
| `recall(query, collection?, top_k?, filter?, score_threshold?)` | Vector search; returns `[{id, score, text, metadata}, ...]`. |
| `forget(ids?, collection?, filter?)` | Deletes points by id or by payload filter. |
| `list_collections()` | Lists collection names. |
| `create_collection(name)` | Creates an empty collection (idempotent). |
| `stats(collection?)` | Returns counts/status for a collection. |

### Chunking behaviour

Long inputs are automatically split before embedding:

- Paragraphs (`\n\n`) → sentences → token windows.
- Default window: **450 tokens** with **50-token overlap** (tunable via `CHUNK_SIZE_TOKENS` and `CHUNK_OVERLAP_TOKENS`).
- Stays safely below the embedder's 512-token context window.
- A 10,000-word document yields roughly 30 chunks; embeddings are produced in batches (`EMBED_BATCH_SIZE`, default 16) against Ollama's `/api/embed`.
- Each chunk's payload includes `text`, `chunk_ordinal`, `chunk_count`, `document_hash`, `created_at`, `source`, and any caller-provided `metadata`, so `recall` results carry enough context to be useful standalone.

### Idempotency

Point IDs are derived as `uuid5(POINT_NS, f"{source}::{sha256(text)}::{ordinal}")`. Re-running `remember` with the same `text` and `source` overwrites existing points instead of duplicating them.

## Ingesting an existing repository

For one-shot bulk ingestion of a codebase you don't want to do via a chat session, Qilin ships with a `qilin ingest` CLI subcommand that talks to Qdrant and Ollama directly (no MCP/SSE round-trips).

### Quick start

The most convenient way is `docker compose run` with the host repo bind-mounted and `qilin` substituted as the entrypoint:

```bash
docker compose run --rm \
  --entrypoint qilin \
  -v "/absolute/path/to/your/repo:/repo:ro" \
  qilin-mcp \
  ingest /repo --collection myrepo
```

PowerShell on Windows:

```powershell
docker compose run --rm `
  --entrypoint qilin `
  -v "C:\path\to\your\repo:/repo:ro" `
  qilin-mcp `
  ingest /repo --collection myrepo
```

You'll see a progress bar; each file is chunked, embedded via Ollama, and upserted into Qdrant. Re-running the same command later is fast: files whose `(source, sha256(content))` is unchanged are skipped.

### What gets ingested

By default:

- **Included extensions:** common code/config/docs extensions (`.py`, `.ts`, `.go`, `.rs`, `.md`, `.toml`, `.yaml`, ...). Override with `--include .proto --include .graphql` (each `--include` adds; passing any switches the list).
- **Excluded directories:** `.git`, `.venv`, `node_modules`, `__pycache__`, `dist`, `build`, etc. Add more with `--exclude infra --exclude vendor`.
- **Excluded files:** common lockfiles (`package-lock.json`, `poetry.lock`, `Cargo.lock`, ...).
- **`.gitignore`** at the repo root is honored unless `--no-respect-gitignore`.
- **Size limit:** files larger than 256KB are skipped (override with `--max-bytes`).

### Useful flags

| Flag | Effect |
|---|---|
| `-c, --collection <name>` | Destination collection. Defaults to `DEFAULT_COLLECTION`. |
| `--source-prefix <s>` | Prepended to each file's `source` (e.g. `qilin/` when sharing one collection across repos). |
| `--include <.ext>` | Add an extension to the include list (repeatable). |
| `--exclude <name>` | Add a directory name to the exclude list (repeatable). |
| `--max-bytes <n>` | Skip files larger than `n` bytes. |
| `--no-respect-gitignore` | Ignore `.gitignore` and ingest everything that passes the other filters. |
| `--force` | Re-ingest even if `(source, content)` is unchanged in the store. |
| `--dry-run` | Print what would be ingested without writing anything. |
| `--git-sha <sha>` | Stamp every chunk's metadata with this SHA. Auto-detected from the repo if omitted. |
| `--label <name>` | Free-form label saved in every chunk's metadata (e.g. `"weekly-snapshot"`). |

### Metadata stamped on every chunk

In addition to the standard `text`, `chunk_ordinal`, `chunk_count`, `document_hash`, `created_at`, `source` fields, the CLI also writes:

- `file_path` — repo-relative POSIX path
- `repo` — directory name of the ingest root
- `language` — inferred from extension (e.g. `python`, `typescript`, `markdown`)
- `size_bytes` — original file size
- `git_sha` — auto-detected commit SHA (if the path is a git working tree)
- `label` — only if `--label` was passed

These can be used at recall time for filtering: `recall(query, filter={"repo": "myrepo", "language": "python"})`.

### Other CLI subcommands

The same `qilin` binary also exposes:

```bash
qilin collections                              # list all collections
qilin stats --collection myrepo                # point count etc.
qilin recall "how does TLS work here?" -c myrepo -k 10
qilin serve                                    # the docker entrypoint uses this
```

### Caveat: stale chunks

`qilin ingest` is idempotent for *unchanged* files (skipped) and *modified* files where the new chunk count >= the old chunk count (overwritten). If you modify a file so that the *new* chunked output has **fewer** chunks than before, the surplus old chunks linger in the collection (their deterministic IDs include `chunk_ordinal` and won't be overwritten). To force a clean ingest of a single source, run `forget` for that source first via the MCP `forget` tool or recreate the collection.

## Configuration

All settings can be overridden via environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Where to reach the embedding model. |
| `EMBEDDING_MODEL` | `nomic-embed-text-v2-moe` | Ollama model name. |
| `EMBEDDING_DIM` | `768` | Must match the model's output dim. |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant HTTP endpoint. |
| `QDRANT_API_KEY` | _(empty)_ | Optional API key. |
| `DEFAULT_COLLECTION` | `memory` | Used when callers omit `collection`. |
| `CHUNK_SIZE_TOKENS` | `450` | Target tokens per chunk. |
| `CHUNK_OVERLAP_TOKENS` | `50` | Overlap between consecutive chunks. |
| `EMBED_BATCH_SIZE` | `16` | Batch size for Ollama `/api/embed`. |
| `MCP_HOST` | `0.0.0.0` | Bind host. |
| `MCP_PORT` | `8443` | Bind port. |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | `/certs/cert.pem`, `/certs/key.pem` | TLS material. |

## Operating notes

- **Persistence:** Qdrant data lives in the `qdrant_data` Docker volume; TLS material lives in `qilin_certs`. Both survive `docker compose down`; use `docker compose down -v` to wipe.
- **Rotating certs:** delete the volume (`docker volume rm qilin_qilin_certs`) and restart; a fresh cert will be generated.
- **Logs:** `docker compose logs -f qilin-mcp`.
- **Local (non-Docker) run:**

  ```bash
  pip install -e .
  uvicorn qilin.server:app --host 0.0.0.0 --port 8443
  ```

  (TLS optional when running outside the container.)

## Development

### Running the test suite

```bash
pip install -e ".[dev]"
ruff check .
pytest --cov --cov-report=term-missing
```

The suite uses mocked Qdrant and Ollama clients, so it runs without any external
services. Coverage is enforced at **80%** via `[tool.coverage.report] fail_under = 80`
in [`pyproject.toml`](pyproject.toml); `pytest` exits non-zero if the bar isn't met.

### Continuous integration

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs `ruff check`
followed by `pytest` (with coverage) on Python 3.11 and 3.12. It is triggered on:

- every `pull_request` targeting `main`
- every `push` to `main`

Coverage reports (`coverage.xml` and `htmlcov/`) are uploaded as workflow
artifacts on each run.

## Out of scope (for now)

- Authentication on the SSE endpoint — add bearer-token middleware in `server.py` if you expose this beyond `localhost`.
- The newer MCP "Streamable HTTP" transport — FastMCP supports it, so adding a `/mcp` endpoint is a small change.
- Hybrid (BM25) search, reranking, and multi-tenant isolation beyond per-collection separation.

## License

MIT.
