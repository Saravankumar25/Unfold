"""Chooses an exporter by name at run time: every hop below is resolved while the program runs."""

import importlib
import sys

from plugins import csv_export


def load(name: str):
    module = importlib.import_module(f"plugins.{name}_export")
    return getattr(module, "export")


def run(kind: str, rows: list[dict[str, str]]) -> str:
    handler = getattr(csv_export, kind)
    return handler(rows)


def dispatch(name: str, payload: list[dict[str, str]]) -> str:
    exporter = load(name)
    return exporter(payload)


def main(argv: list[str]) -> None:
    print(dispatch(argv[1], [{"a": "1"}]))
    print(run(argv[2], [{"a": "1"}]))


if __name__ == "__main__":
    main(sys.argv)
