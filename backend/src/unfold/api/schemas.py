"""HTTP DTOs (LLD §20). The frontend's `lib/api/types.ts` mirrors these exactly; change both together.

Chat SSE payloads are documented here too: every message has `event: <type>` and `data: <json of the model>`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from unfold.diagrams.models import DiagramSpec
from unfold.domain.enums import ChatMode, TechnicalLevel
from unfold.domain.models import ClaimRecord, EvidenceRef
from unfold.guide.models import SectionContent


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


# ---- sessions ----------------------------------------------------------------------------------------------


class SessionOut(BaseModel):
    session_id: str
    created_at: datetime
    expires_at: datetime


class ProjectSummary(BaseModel):
    id: str
    name: str | None
    input_type: str
    source_locator: str | None
    status: str  # job status
    current_stage: str
    created_at: datetime


class SessionCurrentOut(SessionOut):
    projects: list[ProjectSummary] = Field(default_factory=list)


# ---- projects & jobs ---------------------------------------------------------------------------------------


class CreateGithubProject(BaseModel):
    url: str = Field(min_length=10, max_length=300)
    technical_level: TechnicalLevel = TechnicalLevel.INTERMEDIATE
    instruction: str | None = Field(default=None, max_length=1000)


class ProjectCreated(BaseModel):
    project_id: str
    job_id: str


class JobOut(BaseModel):
    id: str
    status: str  # queued | running | succeeded | failed
    current_stage: str
    stage_label: str
    stage_index: int  # position in PIPELINE (0-based); -1 for failure stages
    stage_total: int
    attempt: int
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    degraded: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ProjectOut(BaseModel):
    id: str
    name: str | None
    input_type: str
    source_locator: str | None
    commit_sha: str | None
    default_branch: str | None
    technical_level: str
    instruction: str | None
    project_type: dict[str, Any] | None
    stats: dict[str, Any] | None
    created_at: datetime
    expires_at: datetime
    job: JobOut


class ProgressEvent(BaseModel):
    """One progress event; types: `stage`, `ready`, `failed`, `heartbeat`."""

    type: Literal["stage", "ready", "failed", "heartbeat"]
    stage: str
    label: str
    index: int
    total: int
    detail: str | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool | None = None
    at: datetime


class ProgressItem(ProgressEvent):
    seq: int  # strictly increasing replay cursor


class ProgressPage(BaseModel):
    """`GET /projects/{id}/progress?after=<seq>`: new events, the next cursor, and the durable job state."""

    events: list[ProgressItem]
    cursor: int
    job: JobOut


# ---- guide -------------------------------------------------------------------------------------------------


class GuideSectionSummary(BaseModel):
    key: str
    title: str
    position: int
    status: str
    kind: str
    target_minutes: float
    summary: str | None = None


class GuideOut(BaseModel):
    project_id: str
    estimated_read_minutes: int
    sections: list[GuideSectionSummary]


class GuideSectionOut(BaseModel):
    key: str
    title: str
    position: int
    status: str
    kind: str
    target_minutes: float
    content: SectionContent | None
    claims: list[ClaimRecord]
    evidence: dict[str, EvidenceRef]  # "E3" -> reference (labels used inside the content text)
    quality: dict[str, Any] = Field(default_factory=dict)


class SectionVariantOut(BaseModel):
    key: str
    variant: Literal["simpler", "deeper"]
    content: SectionContent
    evidence: dict[str, EvidenceRef]


class EvidenceOut(BaseModel):
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    language: str | None
    text: str  # redacted
    trust_label: str


# ---- architecture & diagrams -------------------------------------------------------------------------------


class DiagramOut(DiagramSpec):
    mermaid: str
    origin: str = "deterministic"


class SubsystemOut(BaseModel):
    key: str
    title: str
    summary: str | None
    members: list[str]  # file paths


class ArchitectureOut(BaseModel):
    subsystems: list[SubsystemOut]
    counts: dict[str, int]  # files, symbols, endpoints, schemas, services, datastores, nodes, relationships
    languages: list[dict[str, Any]]
    frameworks: list[str]
    diagram: DiagramOut | None


# ---- chat --------------------------------------------------------------------------------------------------


class ChatSessionOut(BaseModel):
    id: str
    project_id: str
    title: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    id: str
    seq: int
    role: Literal["user", "assistant"]
    mode: ChatMode
    text: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime


class ChatDetailOut(BaseModel):
    chat: ChatSessionOut
    messages: list[ChatMessageOut]


class PostChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    mode: ChatMode = ChatMode.DEFAULT
    section_key: str | None = None  # "Ask about this" from a guide section


class ChatEvent(BaseModel):
    """SSE `POST /chats/{id}/messages` — event names equal `type`:

    chat.started {message_id} · retrieval.started · retrieval.completed {detail} · agent.started ·
    agent.tool {tool} · agent.completed · response.delta {text} · response.evidence {evidence} ·
    response.completed {message_id, seq} · error {code, message, retryable}
    """

    type: str
    text: str | None = None
    tool: str | None = None
    detail: str | None = None
    evidence: list[EvidenceRef] | None = None
    message_id: str | None = None
    seq: int | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool | None = None


# ---- exports -----------------------------------------------------------------------------------------------


class ExportCreated(BaseModel):
    artifact_id: str


class ArtifactOut(BaseModel):
    id: str
    kind: str  # guide_pdf | chat_pdf
    status: str  # pending | ready | failed
    error: str | None
    size_bytes: int | None
    created_at: datetime
    expires_at: datetime
    download_url: str | None  # relative, e.g. /api/v1/exports/{id}/download when ready
