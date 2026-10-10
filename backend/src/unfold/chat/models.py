"""Chat contracts (LLD §18). `ChatEvent` is the single event vocabulary of the answer generator; the HTTP layer
decides how to deliver it (one JSON reply, or NDJSON lines)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from unfold.domain.models import EvidenceRef


class ChatEvent(BaseModel):
    """`type` is one of:

    `chat.started` {message_id: the user message id} · `retrieval.completed` {detail} · `response.delta` {text} ·
    `response.evidence` {evidence} · `response.completed` {message_id, seq, text (final, citations resolved),
    evidence, grounding, timings} · `error` {code, message, retryable}
    """

    type: str
    text: str | None = None
    detail: str | None = None
    evidence: list[EvidenceRef] | None = None
    message_id: str | None = None
    seq: int | None = None
    grounding: dict[str, Any] | None = (
        None  # claim grading of the answer (guide.generate.grade_claims quality)
    )
    timings: dict[str, float] | None = None  # retrieval_ms, first_token_ms, total_ms
    code: str | None = None
    message: str | None = None
    retryable: bool | None = None
