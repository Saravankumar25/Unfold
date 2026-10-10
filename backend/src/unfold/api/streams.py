"""Cap on concurrent progress streams per session and overall (audit S-5). In-process: one API process.

Moot once progress is polled (phase 2 removes the SSE route); kept until then.
"""

from __future__ import annotations

from collections import Counter


class StreamLease:
    """One open stream. `release` is idempotent, so it can be wired to several cleanup paths."""

    def __init__(self, cap: StreamCap, key: str) -> None:
        self._cap = cap
        self._key = key
        self._open = True

    def release(self) -> None:
        if self._open:
            self._open = False
            self._cap._free(self._key)


class StreamCap:
    def __init__(self, per_key: int, total: int = 10_000) -> None:
        self._per_key = per_key
        self._total = total
        self._open: Counter[str] = Counter()

    def acquire(self, key: str) -> StreamLease | None:
        if self._open[key] >= self._per_key or sum(self._open.values()) >= self._total:
            return None
        self._open[key] += 1
        return StreamLease(self, key)

    def _free(self, key: str) -> None:
        self._open[key] -= 1
        if self._open[key] <= 0:
            del self._open[key]
