"""`ControlStore` on Postgres: the reference adapter and the local-development default (ADR 0012).

Same queries the API and pipeline used to run inline, moved behind the port. Row locks (`FOR UPDATE`) give the
mutators the same atomicity the DynamoDB adapter gets from a version condition.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from unfold.core.errors import Conflict, NotFound, RateLimited
from unfold.domain.records import (
    ArtifactRec,
    ChatMessageRec,
    ChatRec,
    DiagramRec,
    JobRec,
    ProjectRec,
    SectionRec,
    SessionRec,
    SummaryRec,
)
from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    ArtifactRow,
    ChatMessageRow,
    ChatSessionRow,
    DiagramRow,
    GuidePlanRow,
    GuideSectionRow,
    JobCheckpointRow,
    ProjectRow,
    SessionRow,
    SummaryRow,
)
from unfold.ports import ControlStore, Mutator

_PROJECT_FIELDS = [
    "input_type",
    "source_locator",
    "source_fingerprint",
    "detected_name",
    "default_branch",
    "commit_sha",
    "technical_level",
    "user_instruction",
    "project_type",
    "stats",
    "analysis_version",
    "upload_object_key",
    "ir_object_key",
    "lexical_object_key",
    "created_at",
    "expires_at",
    "deleting_at",
]
_JOB_FIELDS = [
    "kind",
    "section_key",
    "status",
    "current_stage",
    "stage_progress",
    "attempt",
    "error_code",
    "error_detail",
    "retryable",
    "created_at",
    "started_at",
    "completed_at",
    "heartbeat_at",
]
_SECTION_FIELDS = [
    "title",
    "position",
    "status",
    "content",
    "claims",
    "evidence_summary",
    "quality_scores",
    "research",
    "verification",
    "version",
]
_ARTIFACT_FIELDS = [
    "kind",
    "status",
    "object_key",
    "content_type",
    "size_bytes",
    "error",
    "created_at",
    "expires_at",
]
_MESSAGE_FIELDS = ["seq", "role", "mode", "content", "evidence", "model_metadata", "created_at"]
_SUMMARY_FIELDS = ["title", "facts", "semantic", "members", "content_hash", "prompt_version", "model"]
_NON_TERMINAL = ("queued", "running")


def _u(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound("Not found.") from exc


def _session(row: SessionRow) -> SessionRec:
    return SessionRec(
        id=str(row.id),
        token_hash=row.token_hash,
        created_at=row.created_at,
        last_active_at=row.last_active_at,
        expires_at=row.expires_at,
        status=row.status,
    )


def _project(row: ProjectRow) -> ProjectRec:
    return ProjectRec(
        id=str(row.id), session_id=str(row.session_id), **{f: getattr(row, f) for f in _PROJECT_FIELDS}
    )


def _job(row: AnalysisJobRow, session_id: str = "") -> JobRec:
    return JobRec(
        id=str(row.id),
        project_id=str(row.project_id),
        session_id=session_id,
        **{f: getattr(row, f) for f in _JOB_FIELDS},
    )


def _section(row: GuideSectionRow) -> SectionRec:
    return SectionRec(
        project_id=str(row.project_id),
        section_key=row.section_key,
        **{f: getattr(row, f) for f in _SECTION_FIELDS},
    )


def _summary(row: SummaryRow) -> SummaryRec:
    return SummaryRec(
        project_id=str(row.project_id),
        level=row.level,
        key=row.key,
        **{f: getattr(row, f) for f in _SUMMARY_FIELDS},
    )


def _chat(row: ChatSessionRow, count: int) -> ChatRec:
    return ChatRec(
        id=str(row.id),
        project_id=str(row.project_id),
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=count,
    )


def _message(row: ChatMessageRow) -> ChatMessageRec:
    return ChatMessageRec(
        id=str(row.id), chat_id=str(row.chat_session_id), **{f: getattr(row, f) for f in _MESSAGE_FIELDS}
    )


def _artifact(row: ArtifactRow) -> ArtifactRec:
    return ArtifactRec(
        id=str(row.id),
        project_id=str(row.project_id),
        chat_id=None if row.chat_session_id is None else str(row.chat_session_id),
        **{f: getattr(row, f) for f in _ARTIFACT_FIELDS},
    )


def _diagram(row: DiagramRow) -> DiagramRec:
    return DiagramRec(
        id=row.id,
        project_id=str(row.project_id),
        kind=row.kind,
        title=row.title,
        description=row.description,
        spec=row.spec,
        mermaid=row.mermaid,
        origin=row.origin,
        section_key=row.section_key,
    )


class PostgresControlStore(ControlStore):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ping(self) -> None:
        await self._db.ping()

    # --- sessions ---------------------------------------------------------------------------------------

    async def create_session(self, token_hash: str, now: datetime, expires_at: datetime) -> SessionRec:
        row = SessionRow(
            token_hash=token_hash, created_at=now, last_active_at=now, expires_at=expires_at, status="active"
        )
        async with self._db.transaction() as s:
            s.add(row)
            await s.flush()
            return _session(row)

    async def session_by_token(self, token_hash: str) -> SessionRec | None:
        async with self._db.session() as s:
            row = (
                await s.execute(select(SessionRow).where(SessionRow.token_hash == token_hash))
            ).scalar_one_or_none()
        return None if row is None else _session(row)

    async def update_session(self, token_hash: str, fn: Mutator) -> SessionRec:
        async with self._db.transaction() as s:
            row = (
                await s.execute(
                    select(SessionRow).where(SessionRow.token_hash == token_hash).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFound("Session not found.")
            rec = _session(row)
            if fn(rec) is not False:
                row.expires_at, row.last_active_at, row.status = (
                    rec.expires_at,
                    rec.last_active_at,
                    rec.status,
                )
            return rec

    async def expired_sessions(self, now: datetime, limit: int) -> list[SessionRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(select(SessionRow).where(SessionRow.expires_at <= now).limit(limit))
            ).scalars()
            return [_session(r) for r in rows]

    async def delete_session(self, token_hash: str) -> None:
        async with self._db.transaction() as s:
            await s.execute(delete(SessionRow).where(SessionRow.token_hash == token_hash))

    # --- projects and jobs ------------------------------------------------------------------------------

    async def _enforce_caps(self, s: AsyncSession, session_id: str, max_active: int, max_queued: int) -> None:
        if max_active > 0:
            active = await s.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .join(ProjectRow, ProjectRow.id == AnalysisJobRow.project_id)
                .where(ProjectRow.session_id == _u(session_id), AnalysisJobRow.status.in_(_NON_TERMINAL))
            )
            if (active or 0) >= max_active:
                raise RateLimited(
                    f"At most {max_active} analyses can be active per session; wait for one to finish."
                )
        if max_queued > 0:
            queued = await s.scalar(
                select(func.count()).select_from(AnalysisJobRow).where(AnalysisJobRow.status == "queued")
            )
            if (queued or 0) >= max_queued:
                raise RateLimited("The analysis queue is full; try again in a few minutes.")

    async def create_project(
        self,
        project: ProjectRec,
        job: JobRec | None,
        *,
        max_active_per_session: int = 0,
        max_queued_global: int = 0,
    ) -> None:
        async with self._db.transaction() as s:
            if job is not None:
                await self._enforce_caps(s, project.session_id, max_active_per_session, max_queued_global)
            s.add(
                ProjectRow(
                    id=_u(project.id),
                    session_id=_u(project.session_id),
                    **{f: getattr(project, f) for f in _PROJECT_FIELDS},
                )
            )
            await s.flush()
            if job is not None:
                s.add(AnalysisJobRow(id=_u(job.id), project_id=_u(job.project_id), **_job_values(job)))

    async def add_job(
        self, job: JobRec, *, max_active_per_session: int = 0, max_queued_global: int = 0
    ) -> None:
        async with self._db.transaction() as s:
            project = await s.get(ProjectRow, _u(job.project_id), with_for_update=True)
            if project is None:
                raise NotFound("Project not found.")
            existing = await s.execute(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(AnalysisJobRow.project_id == project.id)
            )
            if existing.scalar_one():
                raise Conflict("That record already exists.")
            await self._enforce_caps(s, str(project.session_id), max_active_per_session, max_queued_global)
            s.add(AnalysisJobRow(id=_u(job.id), project_id=_u(job.project_id), **_job_values(job)))

    async def add_regeneration(
        self, job: JobRec, *, max_active_per_session: int = 0, max_queued_global: int = 0
    ) -> tuple[JobRec, bool]:
        async with self._db.transaction() as s:
            # the project row lock serialises concurrent requests and refuses a project deleted since the lookup
            project = await s.get(ProjectRow, _u(job.project_id), with_for_update=True)
            if project is None or project.deleting_at is not None:
                raise NotFound("Project not found.")
            running = (
                await s.execute(
                    select(AnalysisJobRow)
                    .where(
                        AnalysisJobRow.project_id == project.id,
                        AnalysisJobRow.kind == "regenerate",
                        AnalysisJobRow.status.in_(_NON_TERMINAL),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if running is not None:
                return _job(running, str(project.session_id)), False
            await self._enforce_caps(s, str(project.session_id), max_active_per_session, max_queued_global)
            s.add(AnalysisJobRow(id=_u(job.id), project_id=project.id, **_job_values(job)))
        job.session_id = str(project.session_id)
        return job, True

    async def get_project(self, project_id: str) -> ProjectRec | None:
        async with self._db.session() as s:
            row = await s.get(ProjectRow, _u(project_id))
        return None if row is None else _project(row)

    async def update_project(self, project_id: str, fn: Mutator) -> ProjectRec:
        async with self._db.transaction() as s:
            row = await s.get(ProjectRow, _u(project_id), with_for_update=True)
            if row is None:
                raise NotFound("Project not found.")
            rec = _project(row)
            if fn(rec) is not False:
                for f in _PROJECT_FIELDS:
                    setattr(row, f, getattr(rec, f))
            return rec

    async def projects_of_session(self, session_id: str) -> list[tuple[ProjectRec, JobRec | None]]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ProjectRow, AnalysisJobRow)
                    .outerjoin(
                        AnalysisJobRow,
                        (AnalysisJobRow.project_id == ProjectRow.id) & (AnalysisJobRow.kind == "analysis"),
                    )
                    .where(ProjectRow.session_id == _u(session_id))
                    .order_by(ProjectRow.created_at.desc(), AnalysisJobRow.created_at.desc())
                )
            ).all()
        seen: set[str] = set()
        out: list[tuple[ProjectRec, JobRec | None]] = []
        for project, job in rows:
            if str(project.id) in seen:
                continue
            seen.add(str(project.id))
            out.append((_project(project), None if job is None else _job(job, session_id)))
        return out

    async def expired_projects(self, now: datetime, limit: int) -> list[ProjectRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ProjectRow)
                    .where(ProjectRow.expires_at <= now, ProjectRow.deleting_at.is_(None))
                    .limit(limit)
                )
            ).scalars()
            return [_project(r) for r in rows]

    async def deleting_projects(self, limit: int) -> list[ProjectRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(select(ProjectRow).where(ProjectRow.deleting_at.is_not(None)).limit(limit))
            ).scalars()
            return [_project(r) for r in rows]

    async def delete_project_data(self, project_id: str) -> int:
        async with self._db.transaction() as s:
            result = await s.execute(delete(ProjectRow).where(ProjectRow.id == _u(project_id)))
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def get_job(self, job_id: str) -> JobRec | None:
        async with self._db.session() as s:
            row = await s.get(AnalysisJobRow, _u(job_id))
        return None if row is None else _job(row)

    async def latest_job(self, project_id: str, kind: str | None = None) -> JobRec | None:
        stmt = select(AnalysisJobRow).where(AnalysisJobRow.project_id == _u(project_id))
        if kind is not None:
            stmt = stmt.where(AnalysisJobRow.kind == kind)
        async with self._db.session() as s:
            row = (
                await s.execute(stmt.order_by(AnalysisJobRow.created_at.desc()).limit(1))
            ).scalar_one_or_none()
        return None if row is None else _job(row)

    async def jobs_of_project(self, project_id: str) -> list[JobRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(AnalysisJobRow)
                    .where(AnalysisJobRow.project_id == _u(project_id))
                    .order_by(AnalysisJobRow.created_at)
                )
            ).scalars()
            return [_job(r) for r in rows]

    async def update_job(self, job_id: str, fn: Mutator) -> JobRec:
        async with self._db.transaction() as s:
            row = (
                await s.execute(
                    select(AnalysisJobRow).where(AnalysisJobRow.id == _u(job_id)).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFound("Analysis job not found.")
            rec = _job(row)
            if fn(rec) is not False:
                for f in _JOB_FIELDS:
                    setattr(row, f, getattr(rec, f))
            return rec

    async def recoverable_jobs(
        self, *, queued_before: datetime, heartbeat_before: datetime, limit: int
    ) -> list[JobRec]:
        stale_running = (AnalysisJobRow.status == "running") & (
            AnalysisJobRow.heartbeat_at.is_(None) | (AnalysisJobRow.heartbeat_at < heartbeat_before)
        )
        queued = (AnalysisJobRow.status == "queued") & (AnalysisJobRow.created_at < queued_before)
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(AnalysisJobRow)
                    .join(ProjectRow, ProjectRow.id == AnalysisJobRow.project_id)
                    .where(or_(queued, stale_running), ProjectRow.deleting_at.is_(None))
                    .limit(limit)
                )
            ).scalars()
            return [_job(r) for r in rows]

    async def active_job_counts(self) -> tuple[int, int]:
        async with self._db.session() as s:
            rows = (
                await s.execute(select(AnalysisJobRow.status, func.count()).group_by(AnalysisJobRow.status))
            ).all()
        counts = {str(status): int(n) for status, n in rows}
        return int(counts.get("queued", 0)), int(counts.get("running", 0))

    async def checkpoints(self, job_id: str) -> set[str]:
        async with self._db.session() as s:
            res = await s.execute(select(JobCheckpointRow.name).where(JobCheckpointRow.job_id == _u(job_id)))
            return set(res.scalars())

    async def put_checkpoint(self, job_id: str, name: str, data: dict[str, Any]) -> None:
        async with self._db.transaction() as s:
            await s.merge(JobCheckpointRow(job_id=_u(job_id), name=name, data=data))

    # --- guide content ----------------------------------------------------------------------------------

    async def replace_summaries(self, project_id: str, rows: Sequence[SummaryRec]) -> None:
        pid = _u(project_id)
        async with self._db.transaction() as s:
            await s.execute(delete(SummaryRow).where(SummaryRow.project_id == pid))
            s.add_all(
                SummaryRow(
                    project_id=pid, level=r.level, key=r.key, **{f: getattr(r, f) for f in _SUMMARY_FIELDS}
                )
                for r in rows
            )

    async def summaries(self, project_id: str, level: str | None = None) -> list[SummaryRec]:
        stmt = select(SummaryRow).where(SummaryRow.project_id == _u(project_id))
        if level is not None:
            stmt = stmt.where(SummaryRow.level == level)
        async with self._db.session() as s:
            return [_summary(r) for r in (await s.execute(stmt)).scalars()]

    async def replace_plan(
        self, project_id: str, plan: dict[str, Any], planner_version: str, sections: Sequence[SectionRec]
    ) -> None:
        pid = _u(project_id)
        async with self._db.transaction() as s:
            await s.execute(delete(GuideSectionRow).where(GuideSectionRow.project_id == pid))
            await s.merge(GuidePlanRow(project_id=pid, plan=plan, planner_version=planner_version))
            for sec in sections:
                s.add(
                    GuideSectionRow(
                        project_id=pid,
                        section_key=sec.section_key,
                        **{f: getattr(sec, f) for f in _SECTION_FIELDS},
                    )
                )

    async def get_plan(self, project_id: str) -> dict[str, Any] | None:
        async with self._db.session() as s:
            row = await s.get(GuidePlanRow, _u(project_id))
        return None if row is None else dict(row.plan)

    async def sections(self, project_id: str) -> list[SectionRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(GuideSectionRow)
                    .where(GuideSectionRow.project_id == _u(project_id))
                    .order_by(GuideSectionRow.position)
                )
            ).scalars()
            return [_section(r) for r in rows]

    async def get_section(self, project_id: str, key: str) -> SectionRec | None:
        async with self._db.session() as s:
            row = (
                await s.execute(
                    select(GuideSectionRow).where(
                        GuideSectionRow.project_id == _u(project_id), GuideSectionRow.section_key == key
                    )
                )
            ).scalar_one_or_none()
        return None if row is None else _section(row)

    async def update_section(self, project_id: str, key: str, fn: Mutator) -> SectionRec:
        async with self._db.transaction() as s:
            row = (
                await s.execute(
                    select(GuideSectionRow)
                    .where(GuideSectionRow.project_id == _u(project_id), GuideSectionRow.section_key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFound("Section not found.")
            rec = _section(row)
            if fn(rec) is not False:
                for f in _SECTION_FIELDS:
                    setattr(row, f, getattr(rec, f))
            return rec

    async def replace_diagrams(self, project_id: str, rows: Sequence[DiagramRec]) -> None:
        pid = _u(project_id)
        async with self._db.transaction() as s:
            await s.execute(delete(DiagramRow).where(DiagramRow.project_id == pid))
            s.add_all(
                DiagramRow(
                    id=r.id,
                    project_id=pid,
                    kind=r.kind,
                    title=r.title,
                    description=r.description,
                    spec=r.spec,
                    mermaid=r.mermaid,
                    origin=r.origin,
                    section_key=r.section_key,
                )
                for r in rows
            )

    async def diagrams(self, project_id: str, kind: str | None = None) -> list[DiagramRec]:
        stmt = select(DiagramRow).where(DiagramRow.project_id == _u(project_id))
        if kind is not None:
            stmt = stmt.where(DiagramRow.kind == kind)
        async with self._db.session() as s:
            return [_diagram(r) for r in (await s.execute(stmt)).scalars()]

    async def get_diagram(self, project_id: str, diagram_id: str) -> DiagramRec | None:
        async with self._db.session() as s:
            row = await s.get(DiagramRow, diagram_id)
        if row is None or str(row.project_id) != str(_u(project_id)):
            return None
        return _diagram(row)

    # --- chats ------------------------------------------------------------------------------------------

    async def create_chat(self, chat: ChatRec, *, max_per_project: int) -> None:
        pid = _u(chat.project_id)
        async with self._db.transaction() as s:
            # the project row lock serialises concurrent creates, so the per-project cap holds
            project = await s.get(ProjectRow, pid, with_for_update=True)
            if project is None or project.deleting_at is not None:
                raise NotFound("Project not found.")
            count = await s.scalar(
                select(func.count()).select_from(ChatSessionRow).where(ChatSessionRow.project_id == pid)
            )
            if (count or 0) >= max_per_project:
                raise Conflict(
                    f"A project can have at most {max_per_project} chats.", detail={"limit": max_per_project}
                )
            s.add(
                ChatSessionRow(
                    id=_u(chat.id),
                    project_id=pid,
                    title=chat.title,
                    created_at=chat.created_at,
                    updated_at=chat.updated_at,
                )
            )

    @staticmethod
    async def _message_count(s: AsyncSession, chat_id: uuid.UUID) -> int:
        n = await s.scalar(
            select(func.count()).select_from(ChatMessageRow).where(ChatMessageRow.chat_session_id == chat_id)
        )
        return int(n or 0)

    async def get_chat(self, chat_id: str) -> ChatRec | None:
        cid = _u(chat_id)
        async with self._db.session() as s:
            row = await s.get(ChatSessionRow, cid)
            return None if row is None else _chat(row, await self._message_count(s, cid))

    async def chats_of_project(self, project_id: str) -> list[ChatRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ChatSessionRow, func.count(ChatMessageRow.id))
                    .outerjoin(ChatMessageRow, ChatMessageRow.chat_session_id == ChatSessionRow.id)
                    .where(ChatSessionRow.project_id == _u(project_id))
                    .group_by(ChatSessionRow.id)
                    .order_by(ChatSessionRow.updated_at.desc())
                )
            ).all()
        return [_chat(chat, int(n)) for chat, n in rows]

    async def chat_messages(
        self, chat_id: str, *, after_seq: int = 0, last: int | None = None
    ) -> list[ChatMessageRec]:
        stmt = select(ChatMessageRow).where(
            ChatMessageRow.chat_session_id == _u(chat_id), ChatMessageRow.seq > after_seq
        )
        if last is None:
            stmt = stmt.order_by(ChatMessageRow.seq)
        else:
            stmt = stmt.order_by(ChatMessageRow.seq.desc()).limit(max(0, last))
        async with self._db.session() as s:
            rows = list((await s.execute(stmt)).scalars())
        return [_message(r) for r in (reversed(rows) if last is not None else rows)]

    async def append_turn(
        self, chat_id: str, user: ChatMessageRec, assistant: ChatMessageRec, *, title_chars: int
    ) -> int:
        cid = _u(chat_id)
        async with self._db.transaction() as s:
            chat = await s.get(ChatSessionRow, cid, with_for_update=True)  # serialises seq per chat
            if chat is None:
                raise NotFound("Chat not found.")
            last = int(
                await s.scalar(
                    select(func.coalesce(func.max(ChatMessageRow.seq), 0)).where(
                        ChatMessageRow.chat_session_id == cid
                    )
                )
                or 0
            )
            for offset, msg in enumerate((user, assistant), start=1):
                s.add(
                    ChatMessageRow(
                        id=_u(msg.id),
                        chat_session_id=cid,
                        **{f: getattr(msg, f) for f in _MESSAGE_FIELDS if f != "seq"},
                        seq=last + offset,
                    )
                )
            chat.updated_at = datetime.now(UTC)  # the thread list orders by recent activity
            if not chat.title:
                chat.title = " ".join(str(user.content.get("text", "")).split())[:title_chars]
        return last + 2

    # --- exports ----------------------------------------------------------------------------------------

    @staticmethod
    def _pending_like(project_id: uuid.UUID, kind: str, chat_id: str | None, since: datetime) -> Any:
        stmt = select(ArtifactRow).where(
            ArtifactRow.project_id == project_id,
            ArtifactRow.kind == kind,
            ArtifactRow.status == "pending",
            ArtifactRow.created_at > since,
        )
        return stmt.where(
            ArtifactRow.chat_session_id.is_(None)
            if chat_id is None
            else ArtifactRow.chat_session_id == _u(chat_id)
        ).limit(1)

    async def pending_artifact(
        self, project_id: str, kind: str, chat_id: str | None, since: datetime
    ) -> ArtifactRec | None:
        async with self._db.session() as s:
            row = (
                await s.execute(self._pending_like(_u(project_id), kind, chat_id, since))
            ).scalar_one_or_none()
        return None if row is None else _artifact(row)

    async def add_artifact(self, rec: ArtifactRec, *, reuse_since: datetime) -> ArtifactRec:
        pid = _u(rec.project_id)
        async with self._db.transaction() as s:
            # the project row lock serialises concurrent creates and refuses a project deleted since the lookup
            project = await s.get(ProjectRow, pid, with_for_update=True)
            if project is None or project.deleting_at is not None:
                raise NotFound("Project not found.")
            raced = (
                await s.execute(self._pending_like(pid, rec.kind, rec.chat_id, reuse_since))
            ).scalar_one_or_none()
            if raced is not None:
                return _artifact(raced)
            s.add(
                ArtifactRow(
                    id=_u(rec.id),
                    project_id=pid,
                    chat_session_id=None if rec.chat_id is None else _u(rec.chat_id),
                    **{f: getattr(rec, f) for f in _ARTIFACT_FIELDS},
                )
            )
        return rec

    async def get_artifact(self, artifact_id: str) -> ArtifactRec | None:
        async with self._db.session() as s:
            row = await s.get(ArtifactRow, _u(artifact_id))
        return None if row is None else _artifact(row)

    async def artifacts_of_project(self, project_id: str) -> list[ArtifactRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ArtifactRow)
                    .where(ArtifactRow.project_id == _u(project_id))
                    .order_by(ArtifactRow.created_at.desc())
                )
            ).scalars()
            return [_artifact(r) for r in rows]

    async def update_artifact(self, artifact_id: str, fn: Mutator) -> ArtifactRec:
        async with self._db.transaction() as s:
            row = await s.get(ArtifactRow, _u(artifact_id), with_for_update=True)
            if row is None:
                raise NotFound("Export not found.")
            rec = _artifact(row)
            if fn(rec) is not False:
                for f in _ARTIFACT_FIELDS:
                    setattr(row, f, getattr(rec, f))
            return rec

    async def delete_artifact(self, artifact_id: str) -> None:
        async with self._db.transaction() as s:
            await s.execute(delete(ArtifactRow).where(ArtifactRow.id == _u(artifact_id)))

    async def expired_artifacts(self, now: datetime, limit: int) -> list[ArtifactRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(select(ArtifactRow).where(ArtifactRow.expires_at <= now).limit(limit))
            ).scalars()
            return [_artifact(r) for r in rows]

    async def stale_pending_artifacts(
        self, *, created_before: datetime, now: datetime, limit: int
    ) -> list[ArtifactRec]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ArtifactRow)
                    .join(ProjectRow, ProjectRow.id == ArtifactRow.project_id)
                    .where(
                        ArtifactRow.status == "pending",
                        ArtifactRow.created_at <= created_before,
                        ArtifactRow.expires_at > now,
                        ProjectRow.deleting_at.is_(None),
                    )
                    .limit(limit)
                )
            ).scalars()
            return [_artifact(r) for r in rows]


def _job_values(job: JobRec) -> dict[str, Any]:
    return {f: getattr(job, f) for f in _JOB_FIELDS}
