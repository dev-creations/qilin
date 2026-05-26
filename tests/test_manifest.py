"""Tests for the incremental-ingest path (orphan cleanup, --prune)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from qilin import cli as cli_module
from qilin.store import VectorStore, _content_hash


@pytest.fixture(autouse=True)
def _patch_shutdowns(mocker) -> None:
    mocker.patch.object(cli_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(cli_module, "shutdown_store", AsyncMock())


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestScanSources:
    @pytest.mark.asyncio
    async def test_scan_sources_returns_empty_when_collection_missing(
        self, mocker
    ) -> None:
        client = AsyncMock()
        client.collection_exists.return_value = False
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=client)
        store = VectorStore()

        result = await store.scan_sources("c")

        assert result == {}

    @pytest.mark.asyncio
    async def test_scan_sources_groups_by_source_and_hash(self, mocker) -> None:
        client = AsyncMock()
        client.collection_exists.return_value = True

        points_page_one = [
            SimpleNamespace(
                id="p1",
                payload={"source": "a.py", "document_hash": "h1", "chunk_count": 2},
            ),
            SimpleNamespace(
                id="p2",
                payload={"source": "a.py", "document_hash": "h1", "chunk_count": 2},
            ),
            SimpleNamespace(
                id="p3",
                payload={"source": "b.py", "document_hash": "h2", "chunk_count": 1},
            ),
        ]
        client.scroll.return_value = (points_page_one, None)
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=client)
        store = VectorStore()

        result = await store.scan_sources("c")

        assert "a.py" in result
        assert "b.py" in result
        a_entries = result["a.py"]
        assert len(a_entries) == 1
        assert a_entries[0]["document_hash"] == "h1"
        assert a_entries[0]["chunk_count"] == 2
        assert sorted(a_entries[0]["ids"]) == ["p1", "p2"]

    @pytest.mark.asyncio
    async def test_scan_sources_paginates(self, mocker) -> None:
        client = AsyncMock()
        client.collection_exists.return_value = True

        page1 = [
            SimpleNamespace(
                id=f"p{i}",
                payload={"source": "a.py", "document_hash": "h", "chunk_count": 1},
            )
            for i in range(256)
        ]
        page2 = [
            SimpleNamespace(
                id="p_last",
                payload={"source": "z.py", "document_hash": "h", "chunk_count": 1},
            )
        ]
        client.scroll.side_effect = [(page1, "offset"), (page2, None)]
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=client)
        store = VectorStore()

        result = await store.scan_sources("c")

        assert "a.py" in result
        assert "z.py" in result
        assert client.scroll.await_count == 2

    @pytest.mark.asyncio
    async def test_scan_sources_skips_payloads_without_source(self, mocker) -> None:
        client = AsyncMock()
        client.collection_exists.return_value = True
        client.scroll.return_value = (
            [
                SimpleNamespace(id="x", payload={"text": "no source here"}),
                SimpleNamespace(id="y", payload={"source": "a.py", "document_hash": "h"}),
            ],
            None,
        )
        mocker.patch("qilin.store.AsyncQdrantClient", return_value=client)
        store = VectorStore()

        result = await store.scan_sources("c")
        assert list(result.keys()) == ["a.py"]


class TestIngestStaleCleanup:
    def test_ingest_cleans_stale_hash_for_modified_file(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("new content\n")
        # Pretend the collection already has chunks for `a.py` at an older hash.
        fake_store = AsyncMock()
        fake_store.scan_sources.return_value = {
            "a.py": [
                {"document_hash": "OLD-HASH", "chunk_count": 3, "ids": ["o1", "o2", "o3"]},
            ]
        }
        fake_store.chunks_exist.return_value = False
        fake_store.delete.return_value = 3
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path)])

        assert result.exit_code == 0
        fake_store.delete.assert_awaited()
        delete_kwargs = fake_store.delete.await_args.kwargs
        flt = delete_kwargs.get("filter_obj")
        assert flt is not None
        assert flt["source"] == "a.py"
        assert flt["document_hash"] == "OLD-HASH"
        remember_mock.assert_awaited_once()
        assert "stale" in result.stdout.lower() or "cleaned" in result.stdout.lower()

    def test_ingest_skips_when_hash_matches(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        text = "x = 1\n"
        (tmp_path / "a.py").write_text(text)
        new_hash = _content_hash(text)

        fake_store = AsyncMock()
        fake_store.scan_sources.return_value = {
            "a.py": [
                {"document_hash": new_hash, "chunk_count": 1, "ids": ["existing"]},
            ]
        }
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock()
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path)])

        assert result.exit_code == 0
        remember_mock.assert_not_awaited()
        fake_store.delete.assert_not_awaited()

    def test_ingest_prune_deletes_orphaned_sources(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")

        fake_store = AsyncMock()
        fake_store.scan_sources.return_value = {
            "a.py": [{"document_hash": "old", "chunk_count": 1, "ids": ["1"]}],
            "deleted.py": [{"document_hash": "h2", "chunk_count": 3, "ids": ["x", "y", "z"]}],
        }
        fake_store.delete.return_value = 3
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path), "--prune"])

        assert result.exit_code == 0
        delete_filters = [
            call.kwargs.get("filter_obj") for call in fake_store.delete.await_args_list
        ]
        assert any(
            f and f.get("source") == "deleted.py" and "document_hash" not in f
            for f in delete_filters
        )
        assert "prune" in result.stdout.lower() or "deleted" in result.stdout.lower()

    def test_prune_respects_source_prefix(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")

        fake_store = AsyncMock()
        # `other/` has no on-disk file but isn't in our prefix scope.
        fake_store.scan_sources.return_value = {
            "myrepo/a.py": [{"document_hash": "old", "chunk_count": 1, "ids": ["1"]}],
            "other/file.py": [{"document_hash": "h", "chunk_count": 2, "ids": ["x", "y"]}],
        }
        fake_store.delete.return_value = 1
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(
            cli_module.app,
            ["ingest", str(tmp_path), "--source-prefix", "myrepo/", "--prune"],
        )

        assert result.exit_code == 0
        delete_filters = [
            call.kwargs.get("filter_obj") for call in fake_store.delete.await_args_list
        ]
        # other/file.py must NOT be in the delete list because it falls outside the prefix.
        assert not any(
            f and f.get("source") == "other/file.py" for f in delete_filters
        )
