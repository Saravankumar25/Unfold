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

from unfold.api.deps import (
    API_PREFIX,
    CurrentSession,
    RuntimeDep,
    SessionDep,
    enforce_ip_limits,
    enforce_rate_limit,
    owned_chat,
    owned_project,
    parse_uuid,
)
from unfold.api.schemas import ArtifactOut, ErrorEnvelope, ExportCreated
from unfold.core.errors import Conflict, ErrorCode, NotFound, ProjectNotReady, RateLimited, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import ArtifactKind, ArtifactStatus
from unfold.domain.records import ArtifactRec, ProjectRec
from unfold.ports import JobQueue, project_rate_key
from unfold.runtime import Runtime

router = APIRouter(
    tags=["exports"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}},
)
log = get_logger(__name__)

Format = Annotated[Literal["pdf", "md"], Query()]
REUSE_PENDING_WITHIN = timedelta(minutes=10)


def _out(row: ArtifactRec) -> ArtifactOut:
    ready = row.status == ArtifactStatus.READY.value and row.expires_at > datetime.now(UTC)
    return ArtifactOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        error=row.error,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
        expires_at=row.expires_at,
        download_url=f"{API_PREFIX}/exports/{row.id}/download" if ready else None,
    )


async def _create(
    request: Request,
    rt: Runtime,
    session: CurrentSession,
    project: ProjectRec,
    kind: ArtifactKind,
    chat_id: str | None,
) -> ExportCreated:
    cfg = rt.settings
    now = datetime.now(UTC)
    since = now - REUSE_PENDING_WITHIN
    existing = await rt.control.pending_artifact(project.id, kind.value, chat_id, since)
    if existing is not None:
        return ExportCreated(artifact_id=existing.id)
    if not await rt.limiter.hit(project_rate_key("export", project.id), cfg.max_exports_per_hour, 3600):
        raise RateLimited(f"At most {cfg.max_exports_per_hour} exports per project per hour.")
    await enforce_rate_limit(rt, session, "export", cfg.max_exports_per_session_per_hour, 3600)
    await enforce_ip_limits(
        rt, request, "export", cfg.max_exports_per_ip_per_hour, cfg.max_exports_global_per_hour
    )
    rec = ArtifactRec(
        id=str(uuid.uuid4()),
        project_id=project.id,
        chat_id=chat_id,
        kind=kind.value,
        content_type=kind.content_type,
        created_at=now,
        expires_at=now + timedelta(seconds=cfg.export_ttl_seconds),
    )
    stored = await rt.control.add_artifact(rec, reuse_since=since)  # NotFound when the project is going away
    if stored.id != rec.id:  # a concurrent request created the same export first
        return ExportCreated(artifact_id=stored.id)
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_export(rec.id)
    except UnfoldError as exc:  # committed and pending: the sweeper restarts it
        log.warning("export.enqueue_failed", export_id=rec.id, code=exc.code.value)
    return ExportCreated(artifact_id=rec.id)


@router.post("/projects/{project_id}/exports/guide", status_code=202, response_model=ExportCreated)
async def export_guide(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep, format: Format = "pdf"
) -> ExportCreated:
    project = await owned_project(rt, session, project_id)
    if not any(sec.content for sec in await rt.control.sections(project.id)):
        raise ProjectNotReady("The guide is not ready yet.")
    kind = ArtifactKind.GUIDE_PDF if format == "pdf" else ArtifactKind.GUIDE_MD
    return await _create(request, rt, session, project, kind, None)


@router.post("/chats/{chat_id}/exports", status_code=202, response_model=ExportCreated)
async def export_chat(
    chat_id: str, request: Request, rt: RuntimeDep, session: SessionDep, format: Format = "pdf"
) -> ExportCreated:
    chat, project = await owned_chat(rt, session, chat_id)
    if not chat.message_count:
        raise Conflict("The chat has no messages yet.")
    kind = ArtifactKind.CHAT_PDF if format == "pdf" else ArtifactKind.CHAT_MD
    return await _create(request, rt, session, project, kind, chat.id)


async def _owned_artifact(rt: Runtime, session_id: str, artifact_id: str) -> ArtifactRec:
    row = await rt.control.get_artifact(str(parse_uuid(artifact_id)))
    project = await rt.control.get_project(row.project_id) if row else None
    if row is None or project is None or project.session_id != session_id or project.deleting_at is not None:
        raise NotFound("Export not found.")
    if project.expires_at <= datetime.now(UTC):
        raise NotFound("Export not found.")
    return row


@router.get("/exports/{artifact_id}", response_model=ArtifactOut)
async def get_export(artifact_id: str, rt: RuntimeDep, session: SessionDep) -> ArtifactOut:
    return _out(await _owned_artifact(rt, session.id, artifact_id))


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(project_id: str, rt: RuntimeDep, session: SessionDep) -> list[ArtifactOut]:
    project = await owned_project(rt, session, project_id)
    return [_out(r) for r in await rt.control.artifacts_of_project(project.id)]


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
    project = await rt.control.get_project(row.project_id)
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
