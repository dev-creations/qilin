"""Smoke tests for the package version.

The version itself is derived from git tags via setuptools-scm
(see [tool.setuptools_scm] in pyproject.toml). These tests only assert
that the attribute is materialized and looks like a version string -- we
don't pin a specific value so they remain green across releases.
"""

from __future__ import annotations

import re

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
