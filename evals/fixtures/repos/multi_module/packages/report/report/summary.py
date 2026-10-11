"""Summarise an index as text."""

from index.builder import build_index


def summarise(path: str) -> str:
    postings = build_index(path)
    top = sorted(postings, key=lambda word: len(postings[word]), reverse=True)[:3]
    return f"{len(postings)} words, most frequent: {', '.join(top)}"
