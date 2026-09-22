"""SQLAlchemy ORM models = the operational system of record (LLD §4). Schema changes only via Alembic."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _project_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = _now()
    last_active_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    input_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_locator: Mapped[str | None] = mapped_column(Text)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    detected_name: Mapped[str | None] = mapped_column(String(256))
    default_branch: Mapped[str | None] = mapped_column(String(256))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    technical_level: Mapped[str] = mapped_column(String(16), nullable=False)
    user_instruction: Mapped[str | None] = mapped_column(Text)
    project_type: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_object_key: Mapped[str | None] = mapped_column(Text)
    ir_object_key: Mapped[str | None] = mapped_column(Text)
    lexical_object_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisJobRow(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    current_stage: Mapped[str] = mapped_column(String(40), nullable=False, default="RECEIVED")
    stage_progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceFileRow(Base):
    __tablename__ = "source_files"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    language: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_text: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)


class ChunkRow(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[uuid.UUID] = _project_fk()
    path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_id: Mapped[str | None] = mapped_column(String(64))
    symbol_name: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(16), nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    subsystem_id: Mapped[str | None] = mapped_column(String(64))
    graph_node_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    trust: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SummaryRow(Base):
    __tablename__ = "summaries"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    level: Mapped[str] = mapped_column(String(16), primary_key=True)  # file|module|subsystem|project
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    semantic: Mapped[str | None] = mapped_column(Text)
    members: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))


class FindingRow(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trajectory: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()


class GuidePlanRow(Base):
    __tablename__ = "guide_plans"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    planner_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _now()


class GuideSectionRow(Base):
    __tablename__ = "guide_sections"
    __table_args__ = (UniqueConstraint("project_id", "section_key"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    section_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    claims: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DiagramRow(Base):
    __tablename__ = "diagrams"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[uuid.UUID] = _project_fk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mermaid: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="deterministic")
    section_key: Mapped[str | None] = mapped_column(String(64))


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_through: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    learning_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("chat_session_id", "seq"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="default")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    object_key: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="application/pdf")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvalRunRow(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    suite: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()


Index("ix_analysis_jobs_status_heartbeat", AnalysisJobRow.status, AnalysisJobRow.heartbeat_at)
