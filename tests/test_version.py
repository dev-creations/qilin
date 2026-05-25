"""Smoke tests for the package version.

The version itself is derived from git tags via setuptools-scm
(see [tool.setuptools_scm] in pyproject.toml). These tests only assert
that the attribute is materialized and looks like a version string -- we
don't pin a specific value so they remain green across releases.
"""

from __future__ import annotations

import importlib
import re
import sys

import pytest

import qilin


def test_version_attribute_is_non_empty_string() -> None:
    assert isinstance(qilin.__version__, str)
    assert qilin.__version__, "qilin.__version__ should not be empty"


def test_version_looks_like_pep440() -> None:
    # Accept normal releases ("1.2.3"), pre-releases ("1.2.3rc1"),
    # setuptools-scm dev versions ("1.2.4.dev3+gabcdef"), and the
    # fallback ("0.0.0+unknown"). The pattern is permissive on purpose.
    pattern = re.compile(r"^\d+\.\d+\.\d+([.\-+a-zA-Z0-9]*)$")
    assert pattern.match(qilin.__version__), (
        f"qilin.__version__={qilin.__version__!r} does not look like a PEP 440 version"
    )


def test_version_fallback_when_version_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the `except ImportError` arm in src/qilin/__init__.py.

    In normal installs `setuptools-scm` materializes `qilin._version` so the
    try-branch always wins. The fallback fires inside the Docker image build
    (before `pip install` runs setuptools-scm) and in any editable install
    that lacks both git history and the SETUPTOOLS_SCM_PRETEND_VERSION env
    var. We simulate that here by forcing the `_version` import to raise.
    """
    monkeypatch.delitem(sys.modules, "qilin", raising=False)
    monkeypatch.delitem(sys.modules, "qilin._version", raising=False)
    # Setting sys.modules[name] = None makes Python raise ImportError on
    # `from qilin._version import ...` -- documented behavior of the import
    # system's module cache.
    monkeypatch.setitem(sys.modules, "qilin._version", None)

    fresh = importlib.import_module("qilin")
    assert fresh.__version__ == "0.0.0+unknown"
