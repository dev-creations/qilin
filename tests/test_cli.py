"""Tests for the :mod:`qilin.cli` Typer commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from qilin import cli as cli_module


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _patch_shutdowns(mocker) -> None:
    """Replace shutdown helpers so commands don't try to close real clients."""
    mocker.patch.object(cli_module, "shutdown_embedder", AsyncMock())
    mocker.patch.object(cli_module, "shutdown_store", AsyncMock())


def test_help_succeeds(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.app, ["--help"])

    assert result.exit_code == 0
    assert "qilin" in result.stdout.lower()


class TestRecallCommand:
    def test_no_hits(self, runner: CliRunner, mocker) -> None:
        mocker.patch.object(
            cli_module.tools, "recall", AsyncMock(return_value=[])
        )

        result = runner.invoke(cli_module.app, ["recall", "anything"])

        assert result.exit_code == 0
        assert "No hits" in result.stdout

    def test_prints_hits(self, runner: CliRunner, mocker) -> None:
        mocker.patch.object(
            cli_module.tools,
            "recall",
            AsyncMock(
                return_value=[
                    {
                        "id": "p1",
                        "score": 0.87,
                        "text": "hello world",
                        "metadata": {
                            "source": "a.md",
                            "chunk_ordinal": 0,
                            "chunk_count": 1,
                        },
                    }
                ]
            ),
        )

        result = runner.invoke(cli_module.app, ["recall", "hi", "-k", "1"])

        assert result.exit_code == 0
        assert "a.md" in result.stdout
        assert "hello world" in result.stdout

    def test_full_flag_does_not_truncate(self, runner: CliRunner, mocker) -> None:
        long_text = "x" * 1000
        mocker.patch.object(
            cli_module.tools,
            "recall",
            AsyncMock(
                return_value=[
                    {
                        "id": "p1",
                        "score": 0.5,
                        "text": long_text,
                        "metadata": {"source": "long.md"},
                    }
                ]
            ),
        )

        result = runner.invoke(cli_module.app, ["recall", "x", "--full"])

        assert result.exit_code == 0
        assert "..." not in result.stdout.replace("Some(...)", "")


class TestStatsCommand:
    def test_prints_dict_entries(self, runner: CliRunner, mocker) -> None:
        mocker.patch.object(
            cli_module.tools,
            "stats",
            AsyncMock(return_value={"collection": "memory", "exists": True, "points_count": 42}),
        )

        result = runner.invoke(cli_module.app, ["stats"])

        assert result.exit_code == 0
        assert "memory" in result.stdout
        assert "42" in result.stdout


class TestCollectionsCommand:
    def test_prints_names(self, runner: CliRunner, mocker) -> None:
        mocker.patch.object(
            cli_module.tools,
            "list_collections",
            AsyncMock(return_value=["alpha", "beta"]),
        )

        result = runner.invoke(cli_module.app, ["collections"])

        assert result.exit_code == 0
        assert "alpha" in result.stdout
        assert "beta" in result.stdout

    def test_empty_message_when_none(self, runner: CliRunner, mocker) -> None:
        mocker.patch.object(
            cli_module.tools, "list_collections", AsyncMock(return_value=[])
        )

        result = runner.invoke(cli_module.app, ["collections"])

        assert result.exit_code == 0
        assert "no collections" in result.stdout.lower()


class TestIngestCommand:
    def test_nonexistent_path_exits_2(self, runner: CliRunner, tmp_path: Path) -> None:
        missing = tmp_path / "nope"

        result = runner.invoke(cli_module.app, ["ingest", str(missing)])

        assert result.exit_code == 2

    def test_file_path_exits_2(self, runner: CliRunner, tmp_path: Path) -> None:
        f = tmp_path / "a.md"
        f.write_text("hello")

        result = runner.invoke(cli_module.app, ["ingest", str(f)])

        assert result.exit_code == 2

    def test_dry_run_lists_candidates(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("print('hi')\n")
        (tmp_path / "b.md").write_text("# heading\n")
        (tmp_path / "ignore.bin").write_bytes(b"\x00")

        remember_mock = AsyncMock()
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path), "--dry-run"])

        assert result.exit_code == 0
        assert "would ingest" in result.stdout
        assert "a.py" in result.stdout
        assert "b.md" in result.stdout
        remember_mock.assert_not_awaited()

    def test_full_ingest_with_mocked_store(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        (tmp_path / "empty.md").write_text("   \n")

        fake_store = AsyncMock()
        fake_store.chunks_exist.return_value = False
        mocker.patch.object(
            cli_module, "get_store", AsyncMock(return_value=fake_store)
        )

        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        mocker.patch.object(cli_module, "_detect_git_sha", return_value="abc1234")

        result = runner.invoke(
            cli_module.app,
            [
                "ingest",
                str(tmp_path),
                "--collection",
                "test-coll",
                "--label",
                "weekly",
            ],
        )

        assert result.exit_code == 0
        assert "Done" in result.stdout
        remember_mock.assert_awaited()

    def test_ingest_skips_when_chunks_exist(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")

        fake_store = AsyncMock()
        fake_store.chunks_exist.return_value = True
        mocker.patch.object(
            cli_module, "get_store", AsyncMock(return_value=fake_store)
        )

        remember_mock = AsyncMock()
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path)])

        assert result.exit_code == 0
        assert "up-to-date" in result.stdout
        remember_mock.assert_not_awaited()

    def test_ingest_reports_no_candidates(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01")

        fake_store = AsyncMock()
        mocker.patch.object(
            cli_module, "get_store", AsyncMock(return_value=fake_store)
        )

        result = runner.invoke(cli_module.app, ["ingest", str(tmp_path)])

        assert result.exit_code == 0
        assert "Nothing to ingest" in result.stdout

    def test_ingest_respects_gitignore(
        self, runner: CliRunner, tmp_path: Path, mocker
    ) -> None:
        (tmp_path / ".gitignore").write_text("skip_me.py\n")
        (tmp_path / "keep.py").write_text("a = 1\n")
        (tmp_path / "skip_me.py").write_text("b = 2\n")

        fake_store = AsyncMock()
        fake_store.chunks_exist.return_value = False
        mocker.patch.object(
            cli_module, "get_store", AsyncMock(return_value=fake_store)
        )
        remember_mock = AsyncMock(return_value={"chunks_written": 1})
        mocker.patch.object(cli_module.tools, "remember", remember_mock)

        result = runner.invoke(
            cli_module.app, ["ingest", str(tmp_path), "--dry-run"]
        )

        assert result.exit_code == 0
        assert "keep.py" in result.stdout
        assert "skip_me.py" not in result.stdout


class TestHelpers:
    def test_normalize_ext_set_defaults_when_empty(self) -> None:
        default = {".py"}
        assert cli_module._normalize_ext_set([], default) is default

    def test_normalize_ext_set_prefixes_dots_and_lowercases(self) -> None:
        out = cli_module._normalize_ext_set(["PY", ".rs", "  ", "go"], {".py"})
        assert out == {".py", ".rs", ".go"}

    def test_detect_git_sha_returns_none_for_non_git_dir(self, tmp_path: Path) -> None:
        assert cli_module._detect_git_sha(tmp_path) is None

    def test_load_gitignore_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert cli_module._load_gitignore(tmp_path) is None

    def test_load_gitignore_returns_spec_when_present(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.log\n")
        spec = cli_module._load_gitignore(tmp_path)
        assert spec is not None
        assert spec.match_file("foo.log")
        assert not spec.match_file("foo.py")

    def test_load_gitignore_empty_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("")
        assert cli_module._load_gitignore(tmp_path) is None
