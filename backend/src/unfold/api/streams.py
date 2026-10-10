"""Cap on concurrent progress streams per session (audit S-5). In-process: the API is one uvicorn process.

Moot once progress is polled (phase 2 removes the SSE route); kept until then.
"""

from __future__ import annotations

from collections import Counter


class StreamCap:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._open: Counter[str] = Counter()

    def acquire(self, key: str) -> bool:
        if self._open[key] >= self._limit:
            return False
        self._open[key] += 1
        return True

    def release(self, key: str) -> None:
        self._open[key] -= 1
        if self._open[key] <= 0:
            del self._open[key]
