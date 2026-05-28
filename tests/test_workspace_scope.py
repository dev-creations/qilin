from __future__ import annotations

from qilin.config import Settings
from qilin.workspace_scope import (
    apply_path_mappings,
    branch_collection_name,
    extract_workspace_folders_from_ctx,
    normalize_source,
    normalize_workspace_roots,
    project_collection_name,
    reset_workspace_roots,
    resolve_scope,
    sanitize_branch_name,
    set_workspace_roots,
    source_matches_workspace,
)


def test_normalize_workspace_roots_handles_file_uri() -> None:
    roots = normalize_workspace_roots(["file:///tmp/repo/"])
    assert roots == ["/tmp/repo"]


def test_source_matches_workspace_prefix() -> None:
    assert source_matches_workspace("/tmp/repo/src/a.py", ["/tmp/repo"]) is True
    assert source_matches_workspace("/tmp/other/src/a.py", ["/tmp/repo"]) is False


def test_project_collection_name_is_stable() -> None:
    a = project_collection_name("memory", "/tmp/repo")
    b = project_collection_name("memory", "/tmp/repo")
    assert a == b
    assert a.startswith("memory-project-")


def test_resolve_scope_prefix_filter_mode() -> None:
    settings = Settings(workspace_scoping_mode="prefix_filter")
    decision = resolve_scope(
        settings=settings,
        base_collection="memory",
        explicit_workspace_roots=["/tmp/repo"],
    )
    assert decision.collection == "memory"
    assert decision.recall_collections == ["memory"]
    assert decision.apply_prefix_filter is True
    assert decision.workspace_roots == ["/tmp/repo"]


def test_resolve_scope_hybrid_with_project_collection() -> None:
    settings = Settings(
        workspace_scoping_mode="hybrid",
        workspace_use_project_collection=True,
    )
    decision = resolve_scope(
        settings=settings,
        base_collection="memory",
        explicit_workspace_roots=["/tmp/repo"],
    )
    assert decision.collection.startswith("memory-project-")
    assert decision.recall_collections == [decision.collection]
    assert decision.apply_prefix_filter is True


def test_extract_workspace_folders_from_ctx_dict_shape() -> None:
    ctx = {
        "initialize_params": {
            "workspaceFolders": [
                {"uri": "file:///tmp/repo-a", "name": "repo-a"},
                {"uri": "file:///tmp/repo-b", "name": "repo-b"},
            ]
        }
    }
    roots = extract_workspace_folders_from_ctx(ctx)
    assert roots == ["/tmp/repo-a", "/tmp/repo-b"]


def test_apply_path_mappings_exact_and_prefix() -> None:
    mappings = {"/host/work": "/container/work"}
    assert apply_path_mappings("/host/work", mappings) == "/container/work"
    assert apply_path_mappings("/host/work/src/a.py", mappings) == "/container/work/src/a.py"
    assert apply_path_mappings("/other/path", mappings) == "/other/path"


def test_normalize_source_uses_mappings() -> None:
    source = normalize_source("file:///host/work/src/a.py", mappings={"/host/work": "/repo"})
    assert source == "/repo/src/a.py"


def test_set_and_reset_workspace_roots_contextvar() -> None:
    token = set_workspace_roots(["/tmp/repo", "/tmp/repo"])
    try:
        decision = resolve_scope(
            settings=Settings(workspace_scoping_mode="prefix_filter"),
            base_collection="memory",
            explicit_workspace_roots=None,
        )
        assert decision.workspace_roots == ["/tmp/repo"]
    finally:
        reset_workspace_roots(token)


def test_resolve_scope_disabled_returns_base_collection() -> None:
    settings = Settings(workspace_scoping_enabled=False)
    decision = resolve_scope(
        settings=settings,
        base_collection="memory",
        explicit_workspace_roots=["/tmp/repo"],
    )
    assert decision.collection == "memory"
    assert decision.recall_collections == ["memory"]
    assert decision.apply_prefix_filter is False
    assert decision.workspace_roots == []


def test_resolve_scope_per_project_collection_only() -> None:
    settings = Settings(
        workspace_scoping_mode="per_project_collection",
        workspace_use_project_collection=True,
    )
    decision = resolve_scope(
        settings=settings,
        base_collection="memory",
        explicit_workspace_roots=["/tmp/repo"],
    )
    assert decision.collection.startswith("memory-project-")
    assert decision.apply_prefix_filter is False


def test_sanitize_branch_name_rejects_detached_head() -> None:
    assert sanitize_branch_name("HEAD") is None


def test_branch_collection_name_suffix() -> None:
    assert branch_collection_name("memory", "feature-auth", position="suffix") == "memory-feature-auth"


def test_resolve_scope_adds_branch_collections_with_fallback() -> None:
    settings = Settings(
        workspace_scoping_mode="prefix_filter",
        branch_routing_enabled=True,
        branch_fallback_strategy="active_plus_baseline",
        branch_baseline_name="main",
    )
    decision = resolve_scope(
        settings=settings,
        base_collection="memory",
        explicit_workspace_roots=["/tmp/repo"],
        git_branch="feature/auth",
    )
    assert decision.collection.startswith("memory-feature-auth")
    assert len(decision.recall_collections) == 2
    assert decision.recall_collections[1].endswith("-main")


def test_extract_workspace_folders_from_ctx_object_shape() -> None:
    class CtxObj:
        request_context = {
            "initialize_params": {
                "workspaceFolders": [{"uri": "file:///tmp/repo-obj", "name": "repo-obj"}]
            }
        }

    roots = extract_workspace_folders_from_ctx(CtxObj())
    assert roots == ["/tmp/repo-obj"]
