"""Workspace-aware scoping helpers for project-isolated recall."""

from __future__ import annotations

import hashlib
import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from .config import Settings

_workspace_roots_var: ContextVar[tuple[str, ...]] = ContextVar(
    "qilin_workspace_roots", default=()
)


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    raw = path.strip()
    parsed = urlparse(raw)
    if parsed.scheme == "file":
        candidate = unquote(parsed.path or "")
        if os.name == "nt" and candidate.startswith("/") and len(candidate) > 2:
            # file:///C:/repo -> C:/repo
            if candidate[2] == ":":
                candidate = candidate[1:]
    else:
        candidate = raw
    candidate = candidate.replace("\\", "/")
    while "//" in candidate:
        candidate = candidate.replace("//", "/")
    candidate = candidate.rstrip("/")
    if os.name == "nt":
        candidate = candidate.lower()
    return candidate


def apply_path_mappings(path: str, mappings: dict[str, str]) -> str:
    out = path
    for old_prefix, new_prefix in mappings.items():
        old_norm = _normalize_path(old_prefix)
        new_norm = _normalize_path(new_prefix)
        if not old_norm or not new_norm:
            continue
        if out == old_norm:
            return new_norm
        if out.startswith(f"{old_norm}/"):
            return new_norm + out[len(old_norm) :]
    return out


def normalize_workspace_roots(
    roots: list[str] | tuple[str, ...] | None,
    *,
    mappings: dict[str, str] | None = None,
) -> list[str]:
    if not roots:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        normalized = _normalize_path(root)
        if mappings:
            normalized = apply_path_mappings(normalized, mappings)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def normalize_source(source: str | None, *, mappings: dict[str, str] | None = None) -> str:
    if not source:
        return ""
    normalized = _normalize_path(source)
    if mappings:
        normalized = apply_path_mappings(normalized, mappings)
    return normalized


def source_matches_workspace(source: str | None, roots: list[str]) -> bool:
    normalized_source = normalize_source(source)
    if not normalized_source:
        return False
    for root in roots:
        if normalized_source == root or normalized_source.startswith(f"{root}/"):
            return True
    return False


def set_workspace_roots(roots: list[str] | tuple[str, ...]) -> Token[tuple[str, ...]]:
    normalized = tuple(normalize_workspace_roots(roots))
    return _workspace_roots_var.set(normalized)


def reset_workspace_roots(token: Token[tuple[str, ...]]) -> None:
    _workspace_roots_var.reset(token)


def get_workspace_roots() -> list[str]:
    return list(_workspace_roots_var.get())


def extract_workspace_folders_from_ctx(ctx: Any) -> list[str]:
    """Best-effort extraction of workspace folders from FastMCP context objects."""
    if ctx is None:
        return []
    candidate_containers: list[Any] = [ctx]
    for attr in ("request_context", "request", "session", "client", "meta", "metadata"):
        value = getattr(ctx, attr, None)
        if value is not None:
            candidate_containers.append(value)

    roots: list[str] = []
    for container in candidate_containers:
        if isinstance(container, dict):
            roots.extend(_extract_from_mapping(container))
        else:
            data = getattr(container, "__dict__", None)
            if isinstance(data, dict):
                roots.extend(_extract_from_mapping(data))
    return normalize_workspace_roots(roots)


def _extract_from_mapping(data: dict[str, Any]) -> list[str]:
    roots: list[str] = []
    init = data.get("initialize_params") or data.get("initialization_options")
    if isinstance(init, dict):
        roots.extend(_extract_workspace_folders(init.get("workspaceFolders")))
    roots.extend(_extract_workspace_folders(data.get("workspaceFolders")))
    roots.extend(_extract_workspace_folders(data.get("workspace_folders")))
    client_info = data.get("clientInfo") or data.get("client_info")
    if isinstance(client_info, dict):
        roots.extend(_extract_workspace_folders(client_info.get("workspaceFolders")))
    return roots


def _extract_workspace_folders(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
            continue
        if isinstance(item, dict):
            uri = item.get("uri")
            if isinstance(uri, str):
                out.append(uri)
    return out


def project_collection_name(base_collection: str, workspace_root: str) -> str:
    digest = hashlib.sha1(workspace_root.encode("utf-8")).hexdigest()[:10]
    return f"{base_collection}-project-{digest}"


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    collection: str
    recall_collections: list[str]
    workspace_roots: list[str]
    apply_prefix_filter: bool


def sanitize_branch_name(branch: str | None) -> str | None:
    if branch is None:
        return None
    cleaned = branch.strip().lower()
    if not cleaned or cleaned == "head":
        return None
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    if not cleaned:
        return None
    if len(cleaned) > 40:
        digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[:31]}-{digest}"
    return cleaned


def branch_collection_name(base_collection: str, branch: str, *, position: str) -> str:
    if position == "prefix":
        return f"{branch}-{base_collection}"
    return f"{base_collection}-{branch}"


def _build_branch_collections(
    *,
    settings: Settings,
    base_collection: str,
    git_branch: str | None,
) -> list[str]:
    if not settings.branch_routing_enabled:
        return [base_collection]
    active = sanitize_branch_name(git_branch)
    if active is None:
        return [base_collection]
    active_collection = branch_collection_name(
        base_collection, active, position=settings.branch_collection_position
    )
    baseline = sanitize_branch_name(settings.branch_baseline_name)
    baseline_collection = (
        branch_collection_name(
            base_collection, baseline, position=settings.branch_collection_position
        )
        if baseline
        else base_collection
    )
    strategy = settings.branch_fallback_strategy
    if strategy == "active_only":
        return [active_collection]
    if strategy in {"active_plus_baseline", "active_then_baseline"}:
        if active_collection == baseline_collection:
            return [active_collection]
        return [active_collection, baseline_collection]
    return [active_collection]


def resolve_scope(
    *,
    settings: Settings,
    base_collection: str,
    explicit_workspace_roots: list[str] | None = None,
    git_branch: str | None = None,
) -> ScopeDecision:
    if not settings.workspace_scoping_enabled:
        collections = _build_branch_collections(
            settings=settings, base_collection=base_collection, git_branch=git_branch
        )
        return ScopeDecision(
            collection=collections[0],
            recall_collections=collections,
            workspace_roots=[],
            apply_prefix_filter=False,
        )

    roots = normalize_workspace_roots(
        explicit_workspace_roots if explicit_workspace_roots is not None else get_workspace_roots(),
        mappings=settings.workspace_path_mappings,
    )
    if not roots:
        collections = _build_branch_collections(
            settings=settings, base_collection=base_collection, git_branch=git_branch
        )
        return ScopeDecision(
            collection=collections[0],
            recall_collections=collections,
            workspace_roots=[],
            apply_prefix_filter=False,
        )

    mode = settings.workspace_scoping_mode
    collection = base_collection
    apply_prefix = mode in {"prefix_filter", "hybrid"}
    if mode in {"per_project_collection", "hybrid"} and settings.workspace_use_project_collection:
        collection = project_collection_name(base_collection, roots[0])

    collections = _build_branch_collections(
        settings=settings, base_collection=collection, git_branch=git_branch
    )
    return ScopeDecision(
        collection=collections[0],
        recall_collections=collections,
        workspace_roots=roots,
        apply_prefix_filter=apply_prefix,
    )
