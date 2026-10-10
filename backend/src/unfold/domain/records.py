"""Control-plane records returned by the `ControlStore` port (ADR 0012).

Plain data with the attribute names the former ORM rows had, so API and pipeline code reads them unchanged. Ids
are strings; times are timezone-aware. A store never hands out live ORM objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SessionRec:
    id: str
    token_hash: str
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime
    status: str = "active"


@dataclass
class ProjectRec:
    id: str
    session_id: str
    input_type: str
    technical_level: str
    analysis_version: str
    created_at: datetime
    expires_at: datetime
    source_locator: str | None = None
    source_fingerprint: str = ""
    detected_name: str | None = None
    default_branch: str | None = None
    commit_sha: str | None = None
    user_instruction: str | None = None
    project_type: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    upload_object_key: str | None = None
    ir_object_key: str | None = None
    lexical_object_key: str | None = None
    deleting_at: datetime | None = None  # durable delete marker: hidden from every route, cleanup finishes it


@dataclass
class JobRec:
    id: str
    project_id: str
    created_at: datetime
    status: str = "queued"
    current_stage: str = "RECEIVED"
    stage_progress: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    retryable: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    session_id: str = ""  # owner, for the per-session active-job cap
    kind: str = "analysis"  # analysis | regenerate (one chapter redone by a worker)
    section_key: str | None = None  # kind == "regenerate": the chapter

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed")


@dataclass
class SummaryRec:
    project_id: str
    level: str  # file|module|subsystem|project
    key: str
    title: str
    facts: dict[str, Any] = field(default_factory=dict)
    semantic: str | None = None
    members: list[Any] = field(default_factory=list)
    content_hash: str | None = None
    prompt_version: str | None = None
    model: str | None = None


@dataclass
class SectionRec:
    project_id: str
    section_key: str
    title: str
    position: int
    status: str = "pending"
    content: dict[str, Any] | None = None
    claims: list[Any] = field(default_factory=list)
    evidence_summary: dict[str, Any] | None = None
    quality_scores: dict[str, Any] | None = None
    research: dict[str, Any] | None = None  # guide.models.ResearchBrief
    verification: dict[str, Any] | None = None  # guide.verification_models.StoredVerification
    version: int = 1


@dataclass
class DiagramRec:
    id: str
    project_id: str
    kind: str
    title: str
    spec: dict[str, Any]
    mermaid: str
    description: str | None = None
    origin: str = "deterministic"
    section_key: str | None = None


@dataclass
class ChatRec:
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    message_count: int = 0  # derived by the store, never written by callers


@dataclass
class ChatMessageRec:
    id: str
    chat_id: str
    created_at: datetime
    role: str  # user | assistant
    mode: str = "default"
    content: dict[str, Any] = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    model_metadata: dict[str, Any] = field(default_factory=dict)
    seq: int = 0  # assigned by the store when a turn is appended


@dataclass
class ArtifactRec:
    """An export (guide or chat as PDF or Markdown). `object_key` names the blob once `status` is ready."""

    id: str
    project_id: str
    kind: str
    created_at: datetime
    expires_at: datetime
    chat_id: str | None = None
    status: str = "pending"
    object_key: str | None = None
    content_type: str = "application/pdf"
    size_bytes: int | None = None
    error: str | None = None
