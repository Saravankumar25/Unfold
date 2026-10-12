"""Stable, content/context-derived identifiers (LLD §3)."""

from __future__ import annotations

import hashlib
import secrets

_SEP = "\x1f"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _digest(*parts: object, length: int) -> str:
    h = hashlib.sha1(_SEP.join(str(p) for p in parts).encode("utf-8"), usedforsecurity=False)
    return h.hexdigest()[:length]


def entity_id(project_id: str, kind: str, path: str, qualname: str = "") -> str:
    """Deterministic id for an IR/graph entity: recomputation yields the same id (idempotent upserts)."""
    return f"{kind}:{_digest(project_id, kind, path, qualname, length=24)}"


def chunk_id(project_id: str, path: str, start: int, end: int, kind: str) -> str:
    return f"ch:{_digest(project_id, path, start, end, kind, length=24)}"


def diagram_id(project_id: str, kind: str, key: str) -> str:
    return f"dg:{_digest(project_id, kind, key, length=16)}"


def content_hash(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def token_hash(token: str) -> str:
    """Session tokens are stored only as SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
