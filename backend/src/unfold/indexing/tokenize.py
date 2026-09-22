"""Code-aware tokenizer shared by BM25 indexing and querying (LLD §10, AI infra §13).

`verifyAccessToken` → `verifyaccesstoken, verify, access, token`; `auth/service.py` → `auth/service.py, auth,
service, py`; `MAX_RETRY_COUNT` → `max_retry_count, max, retry, count`. Everything is lowercased; a tiny stopword
list is dropped; pure numbers are kept when they have ≥2 digits; single characters are dropped.
"""

from __future__ import annotations

import re

_RAW = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_$.\-/:]*[A-Za-z0-9_$]|[A-Za-z0-9_$]")
_SEGMENT_SPLIT = re.compile(r"[.\-/:]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "that",
        "to",
        "was",
        "with",
        "self",
        "cls",
        "what",
        "where",
        "how",
        "does",
        "do",
        "which",
    }
)


def _keep(token: str) -> bool:
    return len(token) >= 2 and token not in STOPWORDS


def _segment_tokens(segment: str, out: list[str]) -> None:
    seg = segment.strip("_$")
    if not seg:
        return
    low = seg.lower()
    if _keep(low):
        out.append(low)
    parts = [p.lower() for piece in seg.split("_") if piece for p in _CAMEL.findall(piece)]
    if len(parts) > 1 or (parts and parts[0] != low):
        out.extend(p for p in parts if _keep(p))


def tokenize(text: str) -> list[str]:
    """Tokens with repetition preserved (term frequency matters for documents)."""
    out: list[str] = []
    for m in _RAW.finditer(text):
        raw = m.group(0)
        segments = [s for s in _SEGMENT_SPLIT.split(raw) if s]
        if len(segments) > 1:
            low = raw.lower()
            if _keep(low):
                out.append(low)
        for seg in segments:
            _segment_tokens(seg, out)
    return out


def tokenize_query(text: str) -> list[str]:
    """Same transformation as documents, de-duplicated in first-seen order."""
    return list(dict.fromkeys(tokenize(text)))
