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

from . import tools
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
) -> None:
    """Walk PATH and store matching files in Qilin's vector memory.

    By default:

    - .gitignore at the path root is honored;
    - common lockfiles, build outputs, virtualenvs and VCS folders are skipped;
    - files larger than 256KB are skipped;
    - files already stored under the same (source, sha256(content)) are skipped
      (so re-running the command is fast and idempotent).
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
            label=label,
            force=force,
            dry_run=dry_run,
        )
    )


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
    label: str | None,
    force: bool,
    dry_run: bool,
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

        total_chunks = 0
        ingested = 0
        skipped = 0
        empty = 0
        failed: list[tuple[str, str]] = []

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

                try:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    failed.append((source, f"read error: {exc}"))
                    progress.advance(task)
                    continue

                if not text.strip():
                    empty += 1
                    progress.advance(task)
                    continue

                document_hash = _content_hash(text)

                if not force and await store.chunks_exist(collection, source, document_hash):
                    skipped += 1
                    progress.advance(task)
                    continue

                ext = file_path.suffix.lower()
                metadata: dict[str, object] = {
                    "file_path": rel,
                    "repo": repo_root.name,
                    "language": LANG_BY_EXT.get(ext, ext.lstrip(".") or "text"),
                    "size_bytes": file_path.stat().st_size,
                }
                if git_sha:
                    metadata["git_sha"] = git_sha
                if label:
                    metadata["label"] = label

                try:
                    result = await tools.remember(
                        text=text,
                        collection=collection,
                        metadata=metadata,
                        source=source,
                    )
                    total_chunks += int(result.get("chunks_written", 0))
                    ingested += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append((source, type(exc).__name__ + ": " + str(exc)))

                progress.advance(task)

        console.print()
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


@app.command("recall")
def recall_cmd(
    query: Annotated[str, typer.Argument(help="Natural-language query.")],
    collection: Annotated[str | None, typer.Option("--collection", "-c")] = None,
    top_k: Annotated[int, typer.Option("--top-k", "-k")] = 5,
    score_threshold: Annotated[
        float | None,
        typer.Option("--score-threshold", "-s", help="Drop hits below this cosine score (0..1)."),
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
            )
            if not hits:
                console.print("[dim]No hits.[/dim]")
                return
            for i, hit in enumerate(hits, 1):
                meta = hit["metadata"]
                src = meta.get("source") or meta.get("file_path") or "?"
                ordinal = meta.get("chunk_ordinal", "?")
                chunk_count = meta.get("chunk_count", "?")
                console.print(
                    f"[bold cyan]#{i}[/bold cyan] "
                    f"[yellow]{hit['score']:.3f}[/yellow]  "
                    f"[dim]{src}  (chunk {ordinal}/{chunk_count})[/dim]"
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


@app.command("serve")
def serve_cmd() -> None:
    """Launch the MCP SSE server. The docker entrypoint normally does this for you."""
    from . import server as _server

    _server.main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
