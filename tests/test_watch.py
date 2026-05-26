"""Tests for the ``qilin watch`` subcommand and per-collection settings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner
from watchfiles import Change

from qilin import cli as cli_module
from qilin.config import CollectionOverride, Settings


@pytest.fixture(autouse=True)
def _patch_shutdowns(mocker) -> None:
    mocker.patch.object(cli_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(cli_module, "shutdown_store", AsyncMock())


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestWatchCommand:
    def test_watch_missing_path_exits(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            cli_module.app, ["watch", str(tmp_path / "does-not-exist")]
        )
        assert result.exit_code == 2

    def test_watch_runs_until_keyboard_interrupt(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")

        fake_store = AsyncMock()
        fake_store.scan_sources.return_value = {}
        fake_store.delete.return_value = 0
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        new_file = tmp_path / "b.py"

        async def fake_awatch(*_args, **_kwargs):
            new_file.write_text("y = 2\n")
            yield {(Change.added, str(new_file))}
            raise KeyboardInterrupt

        mocker.patch("watchfiles.awatch", fake_awatch)

        result = runner.invoke(cli_module.app, ["watch", str(tmp_path)])

        assert result.exit_code == 0
        remember_mock.assert_awaited()
        sources_ingested = [
            call.kwargs.get("source")
            or (call.args[0] if call.args else None)
            for call in remember_mock.await_args_list
        ]
        assert any(s and s.endswith("b.py") for s in sources_ingested)

    def test_watch_processes_deletes(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        gone = tmp_path / "gone.py"

        fake_store = AsyncMock()
        fake_store.scan_sources.return_value = {
            "gone.py": [{"document_hash": "h", "chunk_count": 1, "ids": ["i"]}],
        }
        fake_store.delete.return_value = 1
        mocker.patch.object(cli_module, "get_store", AsyncMock(return_value=fake_store))

        remember_mock = AsyncMock()
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        async def fake_awatch(*_args, **_kwargs):
            yield {(Change.deleted, str(gone))}
            raise KeyboardInterrupt

        mocker.patch("watchfiles.awatch", fake_awatch)

        result = runner.invoke(cli_module.app, ["watch", str(tmp_path)])

        assert result.exit_code == 0
        delete_filters = [
            call.kwargs.get("filter_obj") for call in fake_store.delete.await_args_list
        ]
        assert any(f and f.get("source") == "gone.py" for f in delete_filters)


class TestPerCollectionConfig:
    def test_for_collection_returns_self_when_no_override(self) -> None:
        s = Settings()
        assert s.for_collection("anything") is s

    def test_for_collection_applies_chunk_overrides(self) -> None:
        s = Settings(
            collections={
                "code": CollectionOverride(
                    chunk_size_tokens=200, chunk_overlap_tokens=10
                )
            }
        )
        scoped = s.for_collection("code")
        assert scoped is not s
        assert scoped.chunk_size_tokens == 200
        assert scoped.chunk_overlap_tokens == 10
        # global defaults preserved on the original
        assert s.chunk_size_tokens != 200

    def test_for_collection_partial_override(self) -> None:
        s = Settings(
            collections={"docs": CollectionOverride(chunk_size_tokens=300)}
        )
        scoped = s.for_collection("docs")
        assert scoped.chunk_size_tokens == 300
        assert scoped.chunk_overlap_tokens == s.chunk_overlap_tokens

    def test_for_collection_empty_override_returns_self(self) -> None:
        s = Settings(collections={"empty": CollectionOverride()})
        scoped = s.for_collection("empty")
        # No fields set => fall back to self (or same values)
        assert scoped.chunk_size_tokens == s.chunk_size_tokens

    def test_remember_uses_per_collection_settings(self, mocker) -> None:
        """End-to-end check: tools.remember threads scoped settings into chunking."""
        import asyncio

        from qilin import tools

        embedder = AsyncMock()
        embedder.embed.return_value = [[0.1, 0.2]]
        store = AsyncMock()
        store.upsert_chunks.return_value = 1

        mocker.patch("qilin.tools.get_embedder", AsyncMock(return_value=embedder))
        mocker.patch("qilin.tools.get_store", AsyncMock(return_value=store))

        s = Settings(
            collections={"tiny": CollectionOverride(chunk_size_tokens=64)}
        )
        mocker.patch("qilin.tools.get_settings", return_value=s)

        spy = mocker.patch("qilin.tools.chunk_code", wraps=tools.chunk_code)

        asyncio.run(
            tools.remember(
                text="hello world. " * 200,
                collection="tiny",
            )
        )

        # chunk_code was called with the scoped settings, not the global ones.
        kwargs = spy.call_args.kwargs
        scoped_settings = kwargs.get("settings")
        assert scoped_settings is not None
        assert scoped_settings.chunk_size_tokens == 64
