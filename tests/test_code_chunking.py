"""Tests for :mod:`qilin.code_chunking`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from qilin import tools
from qilin.chunking import Chunk
from qilin.code_chunking import chunk_code, is_supported

FIXTURES = Path(__file__).parent / "fixtures"


def test_is_supported_recognized_languages() -> None:
    assert is_supported("python")
    assert is_supported("PYTHON")
    assert is_supported("go")
    assert is_supported("typescript")
    assert is_supported("rust")


def test_is_supported_rejects_unknown() -> None:
    assert not is_supported(None)
    assert not is_supported("")
    assert not is_supported("cobol")


def test_unsupported_language_falls_back_to_text_chunks() -> None:
    text = "first paragraph.\n\nsecond paragraph."
    chunks = chunk_code(text, language=None)
    assert chunks
    assert all(c.defines == () for c in chunks)
    assert all(c.imports == () for c in chunks)


class TestPythonChunking:
    def test_extracts_top_level_definitions(self) -> None:
        text = (FIXTURES / "sample.py").read_text()

        chunks = chunk_code(text, language="python", chunk_size_tokens=4000)

        assert chunks
        all_defines = {d for c in chunks for d in c.defines}
        assert "greet" in all_defines
        assert "add" in all_defines
        assert "Counter" in all_defines
        assert "Counter.increment" in all_defines
        assert "Counter.reset" in all_defines

    def test_carries_imports_on_every_chunk(self) -> None:
        text = (FIXTURES / "sample.py").read_text()

        chunks = chunk_code(text, language="python", chunk_size_tokens=4000)

        for chunk in chunks:
            assert "import os" in chunk.imports or "from pathlib import Path" in chunk.imports

    def test_signature_is_first_line_of_first_def(self) -> None:
        text = (FIXTURES / "sample.py").read_text()

        chunks = chunk_code(text, language="python", chunk_size_tokens=4000)

        first_sigs = [c.signature for c in chunks if c.signature]
        assert any(s and "def greet" in s for s in first_sigs)

    def test_splits_class_when_too_big(self) -> None:
        text = (FIXTURES / "sample.py").read_text()

        chunks = chunk_code(text, language="python", chunk_size_tokens=20)

        method_defines = {d for c in chunks for d in c.defines}
        assert "Counter.increment" in method_defines

    def test_chunks_carry_line_spans(self) -> None:
        text = (FIXTURES / "sample.py").read_text()

        chunks = chunk_code(text, language="python", chunk_size_tokens=4000)

        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line


class TestGoChunking:
    def test_extracts_functions_and_types(self) -> None:
        text = (FIXTURES / "sample.go").read_text()

        chunks = chunk_code(text, language="go", chunk_size_tokens=4000)

        assert chunks
        all_defines = {d for c in chunks for d in c.defines}
        assert "Counter" in all_defines
        assert "NewCounter" in all_defines
        assert "greet" in all_defines
        assert "main" in all_defines

    def test_carries_imports(self) -> None:
        text = (FIXTURES / "sample.go").read_text()

        chunks = chunk_code(text, language="go", chunk_size_tokens=4000)

        assert any("fmt" in imp for c in chunks for imp in c.imports)


class TestTypeScriptChunking:
    def test_extracts_function_class_interface_type(self) -> None:
        text = (FIXTURES / "sample.ts").read_text()

        chunks = chunk_code(text, language="typescript", chunk_size_tokens=4000)

        all_defines = {d for c in chunks for d in c.defines}
        assert "greet" in all_defines
        assert "UserStore" in all_defines
        assert "User" in all_defines
        assert "UserOrNull" in all_defines
        assert "UserStore.add" in all_defines

    def test_carries_imports(self) -> None:
        text = (FIXTURES / "sample.ts").read_text()

        chunks = chunk_code(text, language="typescript", chunk_size_tokens=4000)

        assert any("readFile" in imp for c in chunks for imp in c.imports)


class TestFallbacks:
    def test_empty_text_returns_empty(self) -> None:
        assert chunk_code("", language="python") == []

    def test_no_definitions_falls_back_to_text(self) -> None:
        text = "just a doc comment.\n\nsecond paragraph."
        chunks = chunk_code(text, language="python")
        assert chunks
        for chunk in chunks:
            assert isinstance(chunk, Chunk)

    def test_unsupported_language_uses_text_chunker(self) -> None:
        text = "alpha beta gamma. " * 50
        chunks = chunk_code(text, language="brainfuck", chunk_size_tokens=80)
        assert chunks
        assert all(c.defines == () for c in chunks)

    def test_missing_tree_sitter_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force the loader to return None - simulates a missing native wheel.
        import qilin.code_chunking as code_chunking

        monkeypatch.setattr(code_chunking, "_load_parser", lambda _lang: None)
        chunks = code_chunking.chunk_code(
            "def f(): pass\n", language="python", chunk_size_tokens=400
        )
        assert chunks
        assert all(c.defines == () for c in chunks)

    def test_parser_exception_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeParser:
            def parse(self, _source: bytes) -> None:
                raise RuntimeError("boom")

        import qilin.code_chunking as code_chunking

        monkeypatch.setattr(code_chunking, "_load_parser", lambda _lang: FakeParser())
        chunks = code_chunking.chunk_code(
            "def f(): pass\n", language="python", chunk_size_tokens=400
        )
        assert chunks


class TestRoutingFromRemember:
    @pytest.mark.asyncio
    async def test_remember_calls_chunk_code_when_language_set(
        self, monkeypatch: pytest.MonkeyPatch, mocker
    ) -> None:
        embedder = AsyncMock()
        embedder.embed.return_value = [[0.1, 0.2]]
        store = AsyncMock()
        store.upsert_chunks.return_value = 1

        mocker.patch("qilin.tools.get_embedder", AsyncMock(return_value=embedder))
        mocker.patch("qilin.tools.get_store", AsyncMock(return_value=store))

        text = (FIXTURES / "sample.py").read_text()

        result = await tools.remember(
            text=text, source="sample.py", language="python"
        )

        assert result["chunks_written"] == 1
        upsert_call = store.upsert_chunks.await_args
        payloads = upsert_call.kwargs.get("payloads") or upsert_call.args[2]
        assert payloads
        assert any(p.get("defines") for p in payloads)
        assert all(p.get("language") == "python" for p in payloads)
