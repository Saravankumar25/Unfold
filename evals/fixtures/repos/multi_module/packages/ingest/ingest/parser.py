"""Parse text into records."""

from ingest.reader import read_source


def parse_records(path: str) -> list[dict[str, str]]:
    text = read_source(path)
    return [{"line": line} for line in text.splitlines() if line.strip()]
