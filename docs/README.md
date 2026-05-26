# Qilin documentation

This directory holds the in-depth docs that don't fit comfortably in the
top-level [README](../README.md).

## Companions to the README

- [Architecture](architecture.md) - the longer-form system picture.
- [Migrating to 1.0](migrating-to-1.0.md) - the `recall` response shape changed
  in 1.0; this is the upgrade guide.

## Recipes

Short, copy-pasteable recipes for common deployments and workflows.

| Recipe | When you want it |
|---|---|
| [SSE on localhost with a self-signed cert](recipes/sse-on-localhost-with-cert.md) | Hooking Cursor/Claude Desktop into a local Qilin without disabling TLS. |
| [Tuning recall](recipes/tuning-recall.md) | Neighbor expansion, dedupe by source, MMR diversity. |
| [Code-aware search](recipes/code-search.md) | Symbol filters and language-scoped queries over an ingested repo. |
| [Hybrid search and reranking](recipes/hybrid-search.md) | Mixing BM25 with dense vectors and re-ranking with a cross-encoder. |
| [Incremental ingest](recipes/incremental-ingest.md) | Manifests, handling deletions, forcing a clean re-ingest. |
| [Watch mode](recipes/watch-mode.md) | Keeping memory in sync with an editor session. |
| [Code vs. docs collections](recipes/code-vs-docs-collections.md) | Routing code to a code-specific embedding model. |
| [Exposing Qilin on the LAN](recipes/expose-on-lan.md) | TLS trust, bearer-token auth, firewall notes. |
| [Scratch vs. knowledge collections](recipes/scratch-vs-knowledge-collections.md) | TTL-backed session memory beside long-lived knowledge. |
| [Recall feedback loop](recipes/recall-feedback-loop.md) | Logging recalls, marking results useful, and tuning over time. |

Recipes land alongside the features they describe. If a link above 404s, the
feature is on the roadmap; check the [CHANGELOG](../CHANGELOG.md) for what's
already shipped.
