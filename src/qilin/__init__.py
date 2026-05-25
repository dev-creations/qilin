"""Qilin: Qdrant-backed vector memory exposed over MCP/SSE."""

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
