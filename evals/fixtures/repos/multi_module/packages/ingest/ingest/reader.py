"""Read raw files into text."""

from pathlib import Path


def read_source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")
