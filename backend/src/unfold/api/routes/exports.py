"""Export routes (LLD §19, §20): create (202) -> poll status -> download, plus the owner's artifact listing.

Creating an export only writes the artifact row and asks the queue to run the one-shot `export` task; rendering
(Chromium for PDF) never happens in the API process. Everything is owner-scoped: a foreign or deleting project's
artifacts are 404.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from unfold.api.deps import RuntimeDep, SessionDep, owned_chat, owned_project, parse_uuid
from unfold.api.schemas import ArtifactOut, ExportCreated
from unfold.core.errors import ErrorCode, NotFound, ProjectNotReady, RateLimited, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import ArtifactKind, ArtifactStatus
from unfold.persistence.postgres.models import ArtifactRow, ChatMessageRow, GuideSectionRow, ProjectRow
from unfold.ports import JobQueue, project_rate_key
from unfold.runtime import Runtime

router = APIRouter(tags=["exports"])
log = get_logger(__name__)

Format = Annotated[Literal["pdf", "md"], Query()]
REUSE_PENDING_WITHIN = timedelta(minutes=10)


def _out(row: ArtifactRow) -> ArtifactOut:
    ready = row.status == ArtifactStatus.READY.value
    return ArtifactOut(
        id=str(row.id),
        kind=row.kind,
        status=row.status,
        error=row.error,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
        expires_at=row.expires_at,
        download_url=f"/exports/{row.id}/download" if ready else None,
    )


async def _create(
    request: Request, rt: Runtime, project: ProjectRow, kind: ArtifactKind, chat_id: uuid.UUID | None
) -> ExportCreated:
    cfg = rt.settings
    now = datetime.now(UTC)
    async with rt.db.transaction() as s:
        same = select(ArtifactRow).where(
            ArtifactRow.project_id == project.id,
            ArtifactRow.kind == kind.value,
            ArtifactRow.status == ArtifactStatus.PENDING.value,
            ArtifactRow.created_at > now - REUSE_PENDING_WITHIN,
        )
        same = same.where(
            ArtifactRow.chat_session_id.is_(None)
            if chat_id is None
            else ArtifactRow.chat_session_id == chat_id
        )
        existing = (await s.execute(same.limit(1))).scalar_one_or_none()
        if existing is not None:
            return ExportCreated(artifact_id=str(existing.id))
        if not await rt.limiter.hit(project_rate_key("export", project.id), cfg.max_exports_per_hour, 3600):
            raise RateLimited(f"At most {cfg.max_exports_per_hour} exports per project per hour.")
        row = ArtifactRow(
            project_id=project.id,
            chat_session_id=chat_id,
            kind=kind.value,
            status=ArtifactStatus.PENDING.value,
            content_type=kind.content_type,
            expires_at=now + timedelta(seconds=cfg.export_ttl_seconds),
        )
        s.add(row)
        await s.flush()
        artifact_id = str(row.id)
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_export(artifact_id)
    except UnfoldError as exc:  # committed and pending: the sweeper restarts it
        log.warning("export.enqueue_failed", export_id=artifact_id, code=exc.code.value)
    return ExportCreated(artifact_id=artifact_id)


@router.post("/projects/{project_id}/exports/guide", status_code=202, response_model=ExportCreated)
async def export_guide(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep, format: Format = "pdf"
) -> ExportCreated:
    project = await owned_project(rt, session, project_id)
    async with rt.db.session() as s:
        written = (
            await s.execute(
                select(func.count())
                .select_from(GuideSectionRow)
                .where(GuideSectionRow.project_id == project.id, GuideSectionRow.content.is_not(None))
            )
        ).scalar_one()
    if not written:
        raise ProjectNotReady("The guide is not ready yet.")
    kind = ArtifactKind.GUIDE_PDF if format == "pdf" else ArtifactKind.GUIDE_MD
    return await _create(request, rt, project, kind, None)


@router.post("/chats/{chat_id}/exports", status_code=202, response_model=ExportCreated)
async def export_chat(
    chat_id: str, request: Request, rt: RuntimeDep, session: SessionDep, format: Format = "pdf"
) -> ExportCreated:
    chat, project = await owned_chat(rt, session, chat_id)
    async with rt.db.session() as s:
        messages = (
            await s.execute(
                select(func.count())
                .select_from(ChatMessageRow)
                .where(ChatMessageRow.chat_session_id == chat.id)
            )
        ).scalar_one()
    if not messages:
        raise ProjectNotReady("The chat has no messages yet.")
    kind = ArtifactKind.CHAT_PDF if format == "pdf" else ArtifactKind.CHAT_MD
    return await _create(request, rt, project, kind, chat.id)


async def _owned_artifact(rt: Runtime, session_id: uuid.UUID, artifact_id: str) -> ArtifactRow:
    aid = parse_uuid(artifact_id)
    async with rt.db.session() as s:
        row = await s.get(ArtifactRow, aid)
        project = await s.get(ProjectRow, row.project_id) if row else None
    if row is None or project is None or project.session_id != session_id or project.deleting_at is not None:
        raise NotFound("Export not found.")
    return row


@router.get("/exports/{artifact_id}", response_model=ArtifactOut)
async def get_export(artifact_id: str, rt: RuntimeDep, session: SessionDep) -> ArtifactOut:
    return _out(await _owned_artifact(rt, session.id, artifact_id))


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(project_id: str, rt: RuntimeDep, session: SessionDep) -> list[ArtifactOut]:
    project = await owned_project(rt, session, project_id)
    async with rt.db.session() as s:
        rows = (
            (
                await s.execute(
                    select(ArtifactRow)
                    .where(ArtifactRow.project_id == project.id)
                    .order_by(ArtifactRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [_out(r) for r in rows]


@router.get("/exports/{artifact_id}/download", response_model=None)
async def download_export(
    artifact_id: str, rt: RuntimeDep, session: SessionDep
) -> Response | RedirectResponse:
    row = await _owned_artifact(rt, session.id, artifact_id)
    if row.expires_at <= datetime.now(UTC):
        raise UnfoldError(
            "This export has expired. Create a new one.",
            code=ErrorCode.ARTIFACT_EXPIRED,
            http_status=410,
            retryable=False,
        )
    if row.status != ArtifactStatus.READY.value or not row.object_key:
        raise UnfoldError(
            row.error or "The export is not ready yet.",
            code=ErrorCode.ARTIFACT_NOT_READY,
            http_status=409,
            retryable=row.status == ArtifactStatus.PENDING.value,
        )
    kind = ArtifactKind(row.kind)
    async with rt.db.session() as s:
        project = await s.get(ProjectRow, row.project_id)
    slug = re.sub(
        r"[^a-z0-9]+", "-", ((project.detected_name if project else "") or "project").lower()
    ).strip("-")
    filename = f"{slug or 'project'}-{row.kind.replace('_', '-')}.{kind.extension}"
    if rt.settings.export_download == "presigned":
        url = await rt.objects.presigned_get_url(
            row.object_key, rt.settings.export_presigned_ttl_seconds, filename=filename
        )
        return RedirectResponse(url, status_code=302)
    data = await rt.objects.get_bytes(row.object_key)
    return Response(
        data,
        media_type=kind.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
