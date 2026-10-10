"""Secret detection + redaction (LLD §6). Pure functions; applied before any content leaves for an LLM."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

REDACTED = "<REDACTED_SECRET>"

# (name, pattern, group-to-redact). Group 0 = whole match.
_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
        0,
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"), 0),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), 0),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"), 0),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), 0),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), 0),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"), 0),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), 0),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@]+:([^/\s@]{3,})(?=@)"), 1),
]

# KEY = "value" style assignments where the key name looks sensitive.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[A-Za-z0-9_.\-]*(?:secret|passwd|password|pwd|token|api[_-]?key|apikey|access[_-]?key|
        private[_-]?key|client[_-]?secret|auth)[A-Za-z0-9_.\-]*)
    (?P<sep>\s*[:=]\s*|\s*=>\s*)
    (?P<q>["']?)(?P<val>[^\s"'`,;]{8,})(?P=q)
    """
)
# A quoted literal assigned to one of these keys is a credential however guessable it is ('hunter2hunter2').
_STRONG_KEY = re.compile(r"(?i)secret|passwd|password|pwd")
_PLACEHOLDER_HINTS = (
    "example",
    "changeme",
    "your_",
    "your-",
    "xxx",
    "<",
    "${",
    "{{",
    "process.env",
    "os.environ",
    "getenv",
    "settings.",
    "config.",
    "env(",
    "redacted",
    "placeholder",
    "dummy",
)


@dataclass(frozen=True)
class SecretFinding:
    kind: str
    line: int


@dataclass
class Redaction:
    text: str
    findings: list[SecretFinding] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    return any(h in low for h in _PLACEHOLDER_HINTS)


def redact(text: str) -> Redaction:
    """Replace likely secret values with REDACTED, keeping key names so configuration stays explainable."""
    if not text:
        return Redaction(text)
    # (start, end, detector rank, kind); specific patterns rank before the generic assignment rule.
    spans: list[tuple[int, int, int, str]] = []

    for rank, (kind, pattern, group) in enumerate(_PATTERNS):
        for m in pattern.finditer(text):
            start, end = m.span(group)
            spans.append((start, end, rank, kind))

    for m in _ASSIGNMENT.finditer(text):
        val = m.group("val")
        quoted_credential = bool(m.group("q")) and _STRONG_KEY.search(m.group("key")) is not None
        if _looks_like_placeholder(val) or (shannon_entropy(val) < 3.0 and not quoted_credential):
            continue
        start, end = m.span("val")
        spans.append((start, end, len(_PATTERNS), "assignment"))

    if not spans:
        return Redaction(text)
    # Overlapping detections are one secret: merge spans, keep the most specific detector's kind.
    merged: list[tuple[int, int, int, str]] = []
    for s, e, rank, kind in sorted(spans):
        if merged and s <= merged[-1][1]:
            ms, me, mrank, mkind = merged[-1]
            merged[-1] = (ms, max(me, e), *((rank, kind) if rank < mrank else (mrank, mkind)))
        else:
            merged.append((s, e, rank, kind))
    out: list[str] = []
    findings: list[SecretFinding] = []
    cursor = 0
    for s, e, _rank, kind in merged:
        out.append(text[cursor:s])
        out.append(REDACTED)
        findings.append(SecretFinding(kind, _line_of(text, s)))
        cursor = e
    out.append(text[cursor:])
    return Redaction("".join(out), findings)


def redact_text(text: str) -> str:
    return redact(text).text
