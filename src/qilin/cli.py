"""Command-line interface for Qilin.

Exposes a `qilin` console script (installed via the project's `[project.scripts]`
entry) with subcommands:

    qilin ingest <path>     Walk a directory and add files to vector memory.
    qilin recall <query>    Run a similarity search and print top hits.
    qilin stats             Show point counts for a collection.
    qilin serve             Launch the MCP SSE server (same as the docker entrypoint).

The CLI talks to Qdrant and Ollama directly (in-process), so it bypasses the
MCP/SSE transport entirely. This is significantly faster than asking an MCP
client to do batch ingestion file-by-file.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import pathspec
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

from . import analytics, tools
from .config import get_settings
from .embeddings import shutdown_embedder
from .store import _content_hash, get_store, shutdown_store

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="qilin",
    help="Qilin: Qdrant-backed vector memory exposed over MCP/SSE.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


DEFAULT_INCLUDE_EXTS: set[str] = {
    # code
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".java", ".kt", ".scala", ".rs", ".go", ".rb", ".php", ".swift",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql",
    # config
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".env",
    # docs / markup
    ".md", ".rst", ".txt", ".adoc",
    # web
    ".html", ".css", ".scss", ".sass", ".vue", ".svelte",
}

DEFAULT_EXCLUDE_DIRS: set[str] = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env",
    "node_modules", "bower_components",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "target", ".next", ".nuxt", ".turbo",
    ".cache", ".idea", ".vscode",
    "coverage", "htmlcov",
}

# Files that are almost never useful to embed even if they have a recognized extension.
DEFAULT_EXCLUDE_FILES: set[str] = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "uv.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock",
}

LANG_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript", ".cjs": "javascript",
    ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".rs": "rust", ".go": "go", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".m": "objc", ".mm": "objcpp",
    ".sh": "shell", ".bash": "bash", ".zsh": "zsh", ".fish": "fish", ".ps1": "powershell",
    ".sql": "sql",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".env": "env",
    ".md": "markdown", ".rst": "rst", ".txt": "text", ".adoc": "asciidoc",
    ".html": "html", ".css": "css", ".scss": "scss", ".sass": "sass",
    ".vue": "vue", ".svelte": "svelte",
}


def _load_gitignore(repo_root: Path) -> pathspec.PathSpec | None:
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return None
    lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _detect_git_sha(repo_root: Path) -> str | None:
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _detect_git_branch(repo_root: Path) -> str | None:
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip() or None
    if branch == "HEAD":
        return None
    return branch


def _iter_files(
    repo_root: Path,
    *,
    include_exts: set[str],
    exclude_dirs: set[str],
    exclude_files: set[str],
    gitignore: pathspec.PathSpec | None,
    max_bytes: int,
) -> Iterable[Path]:
    for path in repo_root.rglob("*"):
        if path.is_symlink():
            continue
        if any(part in exclude_dirs for part in path.relative_to(repo_root).parts):
            continue
        if not path.is_file():
            continue
        if path.name in exclude_files:
            continue
        if include_exts and path.suffix.lower() not in include_exts:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        if gitignore is not None:
            rel = path.relative_to(repo_root).as_posix()
            if gitignore.match_file(rel):
                continue
        yield path


def _normalize_ext_set(values: list[str], default: set[str]) -> set[str]:
    if not values:
        return default
    out: set[str] = set()
    for v in values:
        v = v.strip().lower()
        if not v:
            continue
        if not v.startswith("."):
            v = "." + v
        out.add(v)
    return out


@app.command("ingest")
def ingest(
    path: Annotated[Path, typer.Argument(help="Directory to ingest (e.g. a repo root).")],
    collection: Annotated[
        str | None,
        typer.Option("--collection", "-c", help="Destination collection. Defaults to the configured DEFAULT_COLLECTION."),
    ] = None,
    source_prefix: Annotated[
        str,
        typer.Option("--source-prefix", help="String prepended to each file's source name. Useful for multi-repo collections (e.g. 'qilin/')."),
    ] = "",
    include: Annotated[
        list[str] | None,
        typer.Option("--include", "-i", help="Extra extension to include (e.g. '.proto'). May be passed multiple times. Pass any to override the default list."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", "-e", help="Extra directory name to skip. May be passed multiple times."),
    ] = None,
    max_bytes: Annotated[
        int,
        typer.Option("--max-bytes", help="Skip files larger than this many bytes."),
    ] = 256_000,
    respect_gitignore: Annotated[
        bool,
        typer.Option("--respect-gitignore/--no-respect-gitignore", help="Honor the repo's .gitignore."),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force/--no-force", help="Re-ingest files even when an identical (source, content) is already stored."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print what would be ingested, then exit."),
    ] = False,
    git_sha: Annotated[
        str | None,
        typer.Option("--git-sha", help="Stamp every chunk's metadata with this git SHA. Auto-detected from the repo if omitted."),
    ] = None,
    label: Annotated[
        str | None,
        typer.Option("--label", help="Free-form label written into every chunk's metadata."),
    ] = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune/--no-prune",
            help=(
                "After ingest, forget any source under the same source_prefix that no longer exists on disk."
            ),
        ),
    ] = False,
) -> None:
    """Walk PATH and store matching files in Qilin's vector memory.

    By default:

    - .gitignore at the path root is honored;
    - common lockfiles, build outputs, virtualenvs and VCS folders are skipped;
    - files larger than 256KB are skipped;
    - files already stored under the same (source, sha256(content)) are skipped;
    - stale chunks from earlier ingests of the *same* source (different content
      hash) are cleaned up automatically.

    Pass ``--prune`` to additionally delete sources that exist in the
    collection but no longer match a file on disk under the active
    ``source_prefix``. Use that for keeping a watched repository in sync.
    """
    repo_root = path.resolve()
    if not repo_root.exists():
        console.print(f"[red]error:[/red] {repo_root} does not exist")
        raise typer.Exit(code=2)
    if not repo_root.is_dir():
        console.print(f"[red]error:[/red] {repo_root} is not a directory")
        raise typer.Exit(code=2)

    settings = get_settings()
    coll = collection or settings.default_collection
    include_exts = _normalize_ext_set(include or [], DEFAULT_INCLUDE_EXTS)
    exclude_dirs = set(exclude or []) | DEFAULT_EXCLUDE_DIRS

    gitignore = _load_gitignore(repo_root) if respect_gitignore else None
    detected_sha = git_sha or _detect_git_sha(repo_root)
    detected_branch = _detect_git_branch(repo_root)

    asyncio.run(
        _run_ingest(
            repo_root=repo_root,
            collection=coll,
            source_prefix=source_prefix,
            include_exts=include_exts,
            exclude_dirs=exclude_dirs,
            exclude_files=DEFAULT_EXCLUDE_FILES,
            max_bytes=max_bytes,
            gitignore=gitignore,
            git_sha=detected_sha,
            git_branch=detected_branch,
            label=label,
            force=force,
            dry_run=dry_run,
            prune=prune,
        )
    )


@dataclass
class _IngestStats:
    """Counters tracked during one ingest run."""

    ingested: int = 0
    skipped: int = 0
    empty: int = 0
    stale_cleaned: int = 0
    pruned: int = 0
    total_chunks: int = 0
    failed: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.failed is None:
            self.failed = []


async def _ingest_one(
    *,
    file_path: Path,
    repo_root: Path,
    collection: str,
    source_prefix: str,
    git_sha: str | None,
    git_branch: str | None,
    label: str | None,
    force: bool,
    existing: dict[str, list[dict[str, object]]],
    store,
    stats: _IngestStats,
) -> None:
    """Ingest a single file. Cleans up stale-hash orphans for the same source."""
    rel = file_path.relative_to(repo_root).as_posix()
    source = f"{source_prefix}{rel}" if source_prefix else rel

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        stats.failed.append((source, f"read error: {exc}"))
        return

    if not text.strip():
        stats.empty += 1
        return

    document_hash = _content_hash(text)
    prior = existing.get(source, [])
    matched = next(
        (e for e in prior if e.get("document_hash") == document_hash),
        None,
    )

    stale_hashes = [
        e["document_hash"]
        for e in prior
        if e.get("document_hash") and e.get("document_hash") != document_hash
    ]

    if matched and not force:
        stats.skipped += 1
        return

    if stale_hashes:
        try:
            deleted = await store.delete(
                collection,
                filter_obj={
                    "source": source,
                    "document_hash": stale_hashes
                    if len(stale_hashes) > 1
                    else stale_hashes[0],
                },
            )
            stats.stale_cleaned += int(deleted or 0)
        except Exception as exc:  # noqa: BLE001
            stats.failed.append((source, f"cleanup error: {exc}"))

    ext = file_path.suffix.lower()
    language = LANG_BY_EXT.get(ext, ext.lstrip(".") or "text")
    metadata: dict[str, object] = {
        "file_path": rel,
        "repo": repo_root.name,
        "size_bytes": file_path.stat().st_size,
    }
    if git_sha:
        metadata["git_sha"] = git_sha
    if git_branch:
        metadata["git_branch"] = git_branch
    if label:
        metadata["label"] = label

    try:
        result = await tools.remember(
            text=text,
            collection=collection,
            metadata=metadata,
            source=source,
            language=language,
            git_branch=git_branch,
        )
        stats.total_chunks += int(result.get("chunks_written", 0))
        stats.ingested += 1
    except Exception as exc:  # noqa: BLE001
        stats.failed.append((source, type(exc).__name__ + ": " + str(exc)))


async def _run_ingest(
    *,
    repo_root: Path,
    collection: str,
    source_prefix: str,
    include_exts: set[str],
    exclude_dirs: set[str],
    exclude_files: set[str],
    max_bytes: int,
    gitignore: pathspec.PathSpec | None,
    git_sha: str | None,
    git_branch: str | None,
    label: str | None,
    force: bool,
    dry_run: bool,
    prune: bool = False,
) -> None:
    candidates = list(
        _iter_files(
            repo_root,
            include_exts=include_exts,
            exclude_dirs=exclude_dirs,
            exclude_files=exclude_files,
            gitignore=gitignore,
            max_bytes=max_bytes,
        )
    )

    console.print()
    console.print(f"[bold]Qilin ingest[/bold]  [dim]{repo_root}[/dim]  ->  [cyan]{collection}[/cyan]")
    console.print(f"  candidates after filters: [bold]{len(candidates)}[/bold]")
    if git_sha:
        console.print(f"  git_sha: {git_sha[:12]}")
    if git_branch:
        console.print(f"  git_branch: {git_branch}")
    if source_prefix:
        console.print(f"  source_prefix: {source_prefix}")
    console.print()

    if dry_run:
        for p in candidates:
            rel = p.relative_to(repo_root).as_posix()
            console.print(f"  [dim]would ingest:[/dim] {source_prefix}{rel}")
        return

    if not candidates:
        console.print("[yellow]Nothing to ingest.[/yellow]")
        return

    try:
        store = await get_store()
        await store.ensure_collection(collection)

        existing = await store.scan_sources(collection)
        stats = _IngestStats()

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("ingesting", total=len(candidates))
            for file_path in candidates:
                rel = file_path.relative_to(repo_root).as_posix()
                source = f"{source_prefix}{rel}" if source_prefix else rel
                progress.update(task, description=f"[dim]{source[-50:]:>50}[/dim]")
                await _ingest_one(
                    file_path=file_path,
                    repo_root=repo_root,
                    collection=collection,
                    source_prefix=source_prefix,
                    git_sha=git_sha,
                    git_branch=git_branch,
                    label=label,
                    force=force,
                    existing=existing,
                    store=store,
                    stats=stats,
                )
                progress.advance(task)

        if prune:
            walked_sources = {
                (
                    f"{source_prefix}{p.relative_to(repo_root).as_posix()}"
                    if source_prefix
                    else p.relative_to(repo_root).as_posix()
                )
                for p in candidates
            }
            for src in list(existing.keys()):
                if source_prefix and not src.startswith(source_prefix):
                    continue
                if src in walked_sources:
                    continue
                try:
                    deleted = await store.delete(
                        collection, filter_obj={"source": src}
                    )
                    stats.pruned += int(deleted or 0)
                except Exception as exc:  # noqa: BLE001
                    stats.failed.append((src, f"prune error: {exc}"))

        # Keep the legacy printout shape but enriched with new counters.
        ingested = stats.ingested
        skipped = stats.skipped
        empty = stats.empty
        total_chunks = stats.total_chunks
        failed = stats.failed or []

        console.print()
        if stats.stale_cleaned:
            console.print(
                f"  cleaned up [bold]{stats.stale_cleaned}[/bold] stale chunks "
                f"(re-ingested files)"
            )
        if stats.pruned:
            console.print(
                f"  pruned [bold]{stats.pruned}[/bold] chunks from deleted sources"
            )
        console.print(
            f"[green]Done.[/green] "
            f"[bold]{ingested}[/bold] ingested "
            f"([bold]{total_chunks}[/bold] chunks), "
            f"[bold]{skipped}[/bold] up-to-date, "
            f"[bold]{empty}[/bold] empty, "
            f"[bold]{len(failed)}[/bold] failed."
        )
        if failed:
            console.print()
            console.print("[red]Failures:[/red]")
            for src, err in failed[:20]:
                console.print(f"  [red]{src}[/red]: {err}")
            if len(failed) > 20:
                console.print(f"  [dim]... and {len(failed) - 20} more[/dim]")
    finally:
        await shutdown_embedder()
        await shutdown_store()


@app.command("watch")
def watch(
    path: Annotated[Path, typer.Argument(help="Directory to watch for changes.")],
    collection: Annotated[
        str | None, typer.Option("--collection", "-c", help="Destination collection.")
    ] = None,
    source_prefix: Annotated[
        str,
        typer.Option("--source-prefix", help="String prepended to each file's source name."),
    ] = "",
    include: Annotated[
        list[str] | None, typer.Option("--include", "-i")
    ] = None,
    exclude: Annotated[
        list[str] | None, typer.Option("--exclude", "-e")
    ] = None,
    max_bytes: Annotated[int, typer.Option("--max-bytes")] = 256_000,
    respect_gitignore: Annotated[
        bool, typer.Option("--respect-gitignore/--no-respect-gitignore")
    ] = True,
    debounce_ms: Annotated[
        int,
        typer.Option(
            "--debounce-ms",
            help="Coalesce filesystem events within this window before re-ingesting.",
        ),
    ] = 500,
    label: Annotated[str | None, typer.Option("--label")] = None,
) -> None:
    """Watch PATH and re-ingest changed files on save.

    Pairs `watchfiles` with the same gitignore / extension / max-bytes filters
    as `qilin ingest`. On save:

    - changed and added files are re-ingested (stale chunks cleaned up);
    - deleted files have their chunks forgotten via a payload filter.

    Press Ctrl+C to stop.
    """
    repo_root = path.resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        console.print(f"[red]error:[/red] {repo_root} is not a directory")
        raise typer.Exit(code=2)

    settings = get_settings()
    coll = collection or settings.default_collection
    include_exts = _normalize_ext_set(include or [], DEFAULT_INCLUDE_EXTS)
    exclude_dirs = set(exclude or []) | DEFAULT_EXCLUDE_DIRS

    gitignore = _load_gitignore(repo_root) if respect_gitignore else None
    detected_sha = _detect_git_sha(repo_root)
    detected_branch = _detect_git_branch(repo_root)

    asyncio.run(
        _run_watch(
            repo_root=repo_root,
            collection=coll,
            source_prefix=source_prefix,
            include_exts=include_exts,
            exclude_dirs=exclude_dirs,
            exclude_files=DEFAULT_EXCLUDE_FILES,
            max_bytes=max_bytes,
            gitignore=gitignore,
            git_sha=detected_sha,
            git_branch=detected_branch,
            label=label,
            debounce_ms=debounce_ms,
        )
    )


async def _run_watch(
    *,
    repo_root: Path,
    collection: str,
    source_prefix: str,
    include_exts: set[str],
    exclude_dirs: set[str],
    exclude_files: set[str],
    max_bytes: int,
    gitignore: pathspec.PathSpec | None,
    git_sha: str | None,
    git_branch: str | None,
    label: str | None,
    debounce_ms: int,
) -> None:
    """Filesystem event loop that incrementally ingests changed files.

    Imported lazily inside the function so ``qilin --help`` works on platforms
    without ``watchfiles`` wheels.
    """
    try:
        from watchfiles import Change, awatch
    except ImportError:
        console.print(
            "[red]error:[/red] `watchfiles` is required for `qilin watch`. "
            "Install with `pip install watchfiles`."
        )
        raise typer.Exit(code=2) from None

    console.print(
        f"[bold]Qilin watch[/bold]  [dim]{repo_root}[/dim]  ->  [cyan]{collection}[/cyan]"
    )
    console.print(f"  debounce: {debounce_ms} ms  (Ctrl+C to stop)")
    console.print()

    store = await get_store()
    await store.ensure_collection(collection)

    def _accept(p: Path) -> bool:
        if not p.is_file():
            return False
        if p.stat().st_size > max_bytes:
            return False
        if p.suffix.lower() not in include_exts:
            return False
        parts = set(p.relative_to(repo_root).parts)
        if parts & exclude_dirs:
            return False
        if p.name in exclude_files:
            return False
        return not (
            gitignore is not None
            and gitignore.match_file(p.relative_to(repo_root).as_posix())
        )

    try:
        async for changes in awatch(
            repo_root,
            step=debounce_ms,
            recursive=True,
            stop_event=None,
        ):
            paths = sorted({Path(p) for _, p in changes})
            to_ingest: list[Path] = []
            to_delete: list[str] = []
            for raw in paths:
                try:
                    rel = raw.relative_to(repo_root).as_posix()
                except ValueError:
                    continue
                source = f"{source_prefix}{rel}" if source_prefix else rel
                event_kinds = {kind for kind, p in changes if Path(p) == raw}
                if Change.deleted in event_kinds and not raw.exists():
                    to_delete.append(source)
                    continue
                if not _accept(raw):
                    continue
                to_ingest.append(raw)

            if not to_ingest and not to_delete:
                continue

            stats = _IngestStats()
            existing = await store.scan_sources(collection)
            current_branch = _detect_git_branch(repo_root) or git_branch
            current_sha = _detect_git_sha(repo_root) or git_sha
            for src in to_delete:
                try:
                    deleted = await store.delete(
                        collection, filter_obj={"source": src}
                    )
                    stats.pruned += int(deleted or 0)
                    console.print(f"  [magenta]-[/magenta] {src}")
                except Exception as exc:  # noqa: BLE001
                    stats.failed.append((src, f"delete error: {exc}"))

            for file_path in to_ingest:
                rel = file_path.relative_to(repo_root).as_posix()
                source = f"{source_prefix}{rel}" if source_prefix else rel
                await _ingest_one(
                    file_path=file_path,
                    repo_root=repo_root,
                    collection=collection,
                    source_prefix=source_prefix,
                    git_sha=current_sha,
                    git_branch=current_branch,
                    label=label,
                    force=False,
                    existing=existing,
                    store=store,
                    stats=stats,
                )
                if stats.ingested:
                    console.print(
                        f"  [green]+[/green] {source}  "
                        f"[dim]({stats.total_chunks} chunks total this batch)[/dim]"
                    )
                    stats.ingested = 0
                    stats.total_chunks = 0
    except KeyboardInterrupt:
        console.print("\n[dim]watch interrupted[/dim]")
    finally:
        await shutdown_embedder()
        await shutdown_store()


@app.command("recall")
def recall_cmd(
    query: Annotated[str, typer.Argument(help="Natural-language query.")],
    collection: Annotated[str | None, typer.Option("--collection", "-c")] = None,
    top_k: Annotated[int, typer.Option("--top-k", "-k")] = 5,
    score_threshold: Annotated[
        float | None,
        typer.Option("--score-threshold", "-s", help="Drop hits below this cosine score (0..1)."),
    ] = None,
    context_window: Annotated[
        int,
        typer.Option(
            "--context-window",
            "-w",
            help="Fetch +/- N sibling chunks per hit (same source) and merge them into one block.",
        ),
    ] = 0,
    group_by_source: Annotated[
        bool,
        typer.Option(
            "--group-by-source/--no-group-by-source",
            help="Keep at most one hit per source (the highest-scoring one).",
        ),
    ] = False,
    mmr_lambda: Annotated[
        float | None,
        typer.Option(
            "--mmr",
            help="Re-rank candidates by MMR diversity. Typical: 0.5 - 0.8. Lower = more diverse.",
        ),
    ] = None,
    full: Annotated[bool, typer.Option("--full", help="Print full chunk text instead of truncating.")] = False,
) -> None:
    """Run a similarity search and print the top hits."""

    async def _go() -> None:
        try:
            hits = await tools.recall(
                query=query,
                collection=collection,
                top_k=top_k,
                score_threshold=score_threshold,
                context_window=context_window,
                group_by_source=group_by_source,
                mmr_lambda=mmr_lambda,
                git_branch=_detect_git_branch(Path.cwd()),
            )
            if not hits:
                console.print("[dim]No hits.[/dim]")
                return
            for i, hit in enumerate(hits, 1):
                extra = hit.get("extra_metadata") or {}
                src = hit.get("source") or extra.get("file_path") or "?"
                ordinal = hit.get("chunk_ordinal", "?")
                chunk_count = hit.get("chunk_count", "?")
                lines = hit.get("lines")
                location = f"{src}:{lines}" if lines else src
                console.print(
                    f"[bold cyan]#{i}[/bold cyan] "
                    f"[yellow]{hit['score']:.3f}[/yellow]  "
                    f"[dim]{location}  (chunk {ordinal}/{chunk_count})[/dim]"
                )
                text = hit["text"]
                if not full and len(text) > 400:
                    text = text[:400] + "..."
                console.print(f"  {text}")
                console.print()
        finally:
            await shutdown_embedder()
            await shutdown_store()

    asyncio.run(_go())


@app.command("stats")
def stats_cmd(
    collection: Annotated[str | None, typer.Option("--collection", "-c")] = None,
) -> None:
    """Print stats for a collection."""

    async def _go() -> None:
        try:
            info = await tools.stats(collection=collection)
            for k, v in info.items():
                console.print(f"  [cyan]{k}[/cyan]: {v}")
        finally:
            await shutdown_embedder()
            await shutdown_store()

    asyncio.run(_go())


@app.command("collections")
def collections_cmd() -> None:
    """List all collections in the vector store."""

    async def _go() -> None:
        try:
            names = await tools.list_collections()
            if not names:
                console.print("[dim](no collections)[/dim]")
            for n in names:
                console.print(f"  [cyan]{n}[/cyan]")
        finally:
            await shutdown_embedder()
            await shutdown_store()

    asyncio.run(_go())


@app.command("recall-log")
def recall_log_cmd(
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help='Only show events newer than e.g. "1h", "30m", "2d", "10s".',
        ),
    ] = None,
    top: Annotated[
        int,
        typer.Option("--top", "-n", help="Limit to the N most recent events."),
    ] = 20,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Override recall log path (defaults to config.recall_log_path).",
        ),
    ] = None,
) -> None:
    """Tail the recall analytics log.

    Each ``recall`` call writes a JSONL event with the query, mode, top_k,
    latency, and the top hits. Use this to audit what an MCP client has
    been asking, find slow queries, or spot recall misses.
    """
    settings = get_settings()
    log_path = Path(path) if path else Path(settings.recall_log_path)
    if not log_path.exists():
        console.print(f"[dim]No recall log at {log_path}[/dim]")
        return

    since_seconds: float | None = None
    if since:
        suffixes = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = since[-1].lower()
        try:
            qty = float(since[:-1]) if unit in suffixes else float(since)
            since_seconds = qty * suffixes.get(unit, 1)
        except ValueError:
            console.print(f"[red]bad --since value: {since}[/red]")
            raise typer.Exit(code=1) from None

    from datetime import datetime as _dt

    cutoff_ts: float | None = None
    if since_seconds is not None:
        cutoff_ts = _dt.now().timestamp() - since_seconds

    events: list[dict] = []
    for event in analytics.iter_log_events(log_path):
        if cutoff_ts is not None:
            ts = event.get("ts")
            if not isinstance(ts, str):
                continue
            try:
                event_ts = _dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if event_ts < cutoff_ts:
                continue
        events.append(event)

    events = events[-top:]
    if not events:
        console.print("[dim](no events)[/dim]")
        return

    for event in events:
        ts = event.get("ts", "?")
        query = event.get("query", "")
        collection = event.get("collection", "")
        mode = event.get("mode", "")
        latency = event.get("latency_ms")
        hits = event.get("hits") or []
        header = f"[cyan]{ts}[/cyan] [dim]{collection}[/dim] [{mode}]"
        if latency is not None:
            header += f" [dim]{latency:.0f}ms[/dim]"
        console.print(header)
        console.print(f"  q: {query!r}")
        for hit in hits[:3]:
            console.print(
                f"    - [yellow]{hit.get('score', 0):.3f}[/yellow]"
                f" {hit.get('source', '<no source>')}"
                f" [dim]({hit.get('id', '?')[:8]})[/dim]"
            )


def _write_hook_script(hook_path: Path, command: str) -> None:
    marker_start = "# >>> qilin auto-index >>>"
    marker_end = "# <<< qilin auto-index <<<"
    block = f"{marker_start}\n{command}\n{marker_end}\n"
    existing = hook_path.read_text(encoding="utf-8", errors="ignore") if hook_path.exists() else ""
    if marker_start in existing and marker_end in existing:
        before, _, tail = existing.partition(marker_start)
        _, _, after = tail.partition(marker_end)
        new_content = before + block + after.lstrip("\n")
    else:
        shebang = "#!/usr/bin/env sh\n"
        prefix = shebang if not existing.startswith("#!") else ""
        spacer = "\n" if existing and not existing.endswith("\n") else ""
        new_content = f"{prefix}{existing}{spacer}{block}"
    hook_path.write_text(new_content, encoding="utf-8")
    with suppress(OSError):
        hook_path.chmod(0o755)


@app.command("install-git-hooks")
def install_git_hooks(
    path: Annotated[
        Path, typer.Argument(help="Git repository path where hooks will be installed.")
    ] = Path("."),
    collection: Annotated[
        str | None, typer.Option("--collection", "-c", help="Collection to ingest into.")
    ] = None,
) -> None:
    """Install post-checkout and post-commit hooks for incremental indexing."""
    repo_root = path.resolve()
    git_dir = repo_root / ".git"
    hooks_dir = git_dir / "hooks"
    if not git_dir.exists() or not hooks_dir.exists():
        console.print(f"[red]error:[/red] {repo_root} is not a git repository")
        raise typer.Exit(code=2)
    settings = get_settings()
    collection_name = collection or settings.default_collection
    ingest_cmd = (
        f'qilin ingest "{repo_root}" --collection "{collection_name}" '
        "--respect-gitignore --prune"
    )
    post_checkout = hooks_dir / "post-checkout"
    post_commit = hooks_dir / "post-commit"
    _write_hook_script(post_checkout, ingest_cmd)
    _write_hook_script(post_commit, ingest_cmd)
    console.print("[green]Installed git hooks:[/green]")
    console.print(f"  {post_checkout}")
    console.print(f"  {post_commit}")


@app.command("serve")
def serve_cmd() -> None:
    """Launch the MCP SSE server. The docker entrypoint normally does this for you."""
    from . import server as _server

    _server.main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
