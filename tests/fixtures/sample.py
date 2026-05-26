"""Sample Python file used by code-chunker tests."""

from __future__ import annotations

import os
from pathlib import Path


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"hello, {name}"


def add(a: int, b: int) -> int:
    return a + b


class Counter:
    """A tiny stateful object."""

    def __init__(self, start: int = 0) -> None:
        self.value = start

    def increment(self) -> None:
        self.value += 1

    def decrement(self) -> None:
        self.value -= 1

    def reset(self) -> None:
        self.value = 0


CONSTANT = 42
