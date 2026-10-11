"""Build an inverted index from parsed records."""

from ingest.parser import parse_records


def build_index(path: str) -> dict[str, list[int]]:
    postings: dict[str, list[int]] = {}
    for number, record in enumerate(parse_records(path)):
        for word in record["line"].split():
            postings.setdefault(word.lower(), []).append(number)
    return postings
