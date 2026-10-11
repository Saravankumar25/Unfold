"""Note operations on top of the storage module."""

from notekeeper.storage import load_notes, save_notes


def add_note(text: str) -> dict[str, object]:
    notes = load_notes()
    note: dict[str, object] = {"id": len(notes) + 1, "text": text}
    save_notes([*notes, note])
    return note


def list_notes() -> list[dict[str, object]]:
    return load_notes()


def find_notes(word: str) -> list[dict[str, object]]:
    return [n for n in load_notes() if word.lower() in str(n["text"]).lower()]
