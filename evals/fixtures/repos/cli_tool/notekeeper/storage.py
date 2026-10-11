"""The JSON file the notes live in."""

import json
from pathlib import Path

NOTES_FILE = Path.home() / ".notekeeper.json"


def load_notes() -> list[dict[str, object]]:
    if not NOTES_FILE.exists():
        return []
    return json.loads(NOTES_FILE.read_text(encoding="utf-8"))


def save_notes(notes: list[dict[str, object]]) -> None:
    NOTES_FILE.write_text(json.dumps(notes), encoding="utf-8")
