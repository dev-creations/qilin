# Branch-aware routing

Use branch-aware routing to isolate memory per git branch while still allowing controlled fallback to a baseline branch.

## Configuration

Set these environment variables (or equivalent `.env` keys):

```bash
BRANCH_ROUTING_ENABLED=true
BRANCH_BASELINE_NAME=main
BRANCH_FALLBACK_STRATEGY=active_plus_baseline
BRANCH_COLLECTION_POSITION=suffix
```

`BRANCH_FALLBACK_STRATEGY` options:

- `active_only`: query only the active branch collection.
- `active_plus_baseline`: always query active branch, then baseline branch.
- `active_then_baseline`: query active branch first, then baseline only when active results are insufficient for `top_k`.

## Ingest and watch

`qilin ingest` and `qilin watch` auto-detect the current branch using:

```bash
git rev-parse --abbrev-ref HEAD
```

When branch routing is enabled, collection names are derived automatically, for example:

- base collection: `memory`
- active branch: `feature/auth`
- resolved collection: `memory-feature-auth`

## Recall behavior

`qilin recall` and MCP recall tools accept `git_branch` and optional `fallback_strategy`. When branch routing is enabled, recall queries one or more collections according to strategy and prioritizes active-branch hits during merge/dedup.

## Install git hooks

Install auto-index hooks in a repository:

```bash
qilin install-git-hooks /path/to/repo --collection memory
```

This installs/updates:

- `.git/hooks/post-checkout`
- `.git/hooks/post-commit`

Each hook runs incremental ingest with prune so branch switches and commits refresh indexed memory.
