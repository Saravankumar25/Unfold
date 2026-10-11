from notekeeper.core import find_notes


def test_find_notes_is_case_insensitive() -> None:
    assert find_notes("zzz-not-there") == []
