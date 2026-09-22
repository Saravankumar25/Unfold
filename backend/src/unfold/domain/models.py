"""Shared domain value objects (LLD §8, §16, §21). Pure pydantic; no I/O. Imported by every layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from unfold.domain.enums import ClaimKind, ClaimStatus


class Provenance(BaseModel):
    """Where a fact came from. Deterministic facts outrank inferred ones everywhere (ESD §21)."""

    source: Literal["deterministic", "inferred"] = "deterministic"
    analyzer: str
    confidence: float = 1.0
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimRecord(BaseModel):
    text: str
    kind: ClaimKind = ClaimKind.PROJECT_FACT
    status: ClaimStatus = ClaimStatus.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)  # chunk ids
    graph_node_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    note: str | None = None


class EvidenceRef(BaseModel):
    """A pointer to redacted repository text that a UI or PDF can show as 'View evidence'."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str | None = None
    score: float | None = None


class SessionProfile(BaseModel):
    technical_level: str = "intermediate"
    instruction: str | None = None
