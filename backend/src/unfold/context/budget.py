"""Token estimation + budgeting for compiled contexts (LLD §12). Tokens ≈ ceil(chars / 4)."""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 4
TRUNCATION_MARKER = "\n… [truncated]"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def truncate_to_tokens(text: str, max_tokens: int, *, marker: str = TRUNCATION_MARKER) -> str:
    """Cut `text` at a line boundary so that result (incl. marker) fits `max_tokens`. '' if nothing fits."""
    if estimate_tokens(text) <= max_tokens:
        return text
    budget_chars = max_tokens * CHARS_PER_TOKEN - len(marker)
    if budget_chars <= 0:
        return ""
    cut = text[:budget_chars]
    newline = cut.rfind("\n")
    if newline > budget_chars // 2:  # prefer a whole-line cut when it keeps at least half the room
        cut = cut[:newline]
    cut = cut.rstrip()
    return f"{cut}{marker}" if cut else ""


class TokenBudget:
    """A running token allowance. `take` succeeds only when the item fits entirely."""

    def __init__(self, total: int) -> None:
        if total < 0:
            raise ValueError("budget must be >= 0")
        self.total = total
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def fits(self, tokens: int) -> bool:
        return tokens <= self.remaining

    def take(self, tokens: int) -> bool:
        if not self.fits(tokens):
            return False
        self.used += tokens
        return True

    def force(self, tokens: int) -> None:
        """Account for mandatory content (objective) even if it overflows."""
        self.used += tokens

    def release(self, tokens: int) -> None:
        self.used = max(0, self.used - tokens)
