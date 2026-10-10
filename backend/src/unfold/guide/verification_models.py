"""Verification contracts (investigation engine §4, task 3.7). Pure value objects; no I/O."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from unfold.domain.models import ClaimRecord

CheckName = Literal[
    "path_exists", "line_in_bounds", "symbol_exists", "excerpt_matches", "edge_exists", "claim_supported"
]
Verdict = Literal["accept", "revise", "accept_with_gaps"]
MAX_REVISION_ROUNDS = 2
SEMANTIC_DIMENSIONS = ("coverage", "depth", "coherence", "specificity", "redundancy")
NOT_CONFIRMED = "Not confirmed in the code:"  # the frontend matches this exact prefix


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: CheckName
    ok: bool
    subject: str  # what was checked: "path:12-40", "a -> b", a claim's text
    block: str | None = None  # `b<index>` of the content block it belongs to; None when it cannot be placed
    detail: str | None = None


class SemanticCritique(BaseModel):
    """What the critique model may say: scores and block ids. It cannot carry replacement text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage: float = Field(ge=0, le=1)
    depth: float = Field(ge=0, le=1)
    coherence: float = Field(ge=0, le=1)
    specificity: float = Field(ge=0, le=1)
    redundancy: float = Field(ge=0, le=1)  # 1 = nothing repeated
    failing_blocks: list[str] = Field(default_factory=list, max_length=32)


class VerificationResult(BaseModel):
    deterministic: list[CheckResult]
    claims: list[ClaimRecord] = Field(default_factory=list)  # `grade_claims` output
    semantic: dict[str, float] | None = None
    failing_blocks: list[str]  # deterministic and critique failures, in block order
    verdict: Verdict


class Unsupported(BaseModel):
    """A block that still fails after the revision cap. `has_evidence`: it cites at least one real location."""

    model_config = ConfigDict(frozen=True)

    block: str
    has_evidence: bool
