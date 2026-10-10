"""Project creation, status, deletion and replayable progress (LLD §4 "Project creation", §20).

Creation commits project + job first, then enqueues (outbox-equivalent: the stale-job sweeper re-enqueues
`queued` jobs that never started, so a crash between commit and enqueue loses nothing).
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from sqlalchemy import select

from unfold import ANALYSIS_VERSION
from unfold.api.deps import (
    CurrentSession,
    RuntimeDep,
    SessionDep,
    enforce_ip_limits,
    enforce_rate_limit,
    owned_project,
)
from unfold.api.schemas import (
    CreateGithubProject,
    JobOut,
    ProgressItem,
    ProgressPage,
    ProjectCreated,
    ProjectOut,
)
from unfold.cleanup.service import mark_deleting
from unfold.core.errors import ErrorCode, InputError, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import InputType, JobStage, JobStatus, TechnicalLevel
from unfold.ingestion.archive import ZIP_MAGIC
from unfold.ingestion.github import parse_github_url
from unfold.orchestration.progress import STAGE_TOTAL, stage_event
from unfold.persistence.postgres.models import AnalysisJobRow, ProjectRow, SessionRow
from unfold.ports import JobQueue
from unfold.runtime import Runtime

router = APIRouter(prefix="/projects", tags=["projects"])
log = get_logger(__name__)


def job_out(job: AnalysisJobRow) -> JobOut:
    stage = JobStage(job.current_stage)
    detail = job.error_detail or {}
    return JobOut(
        id=str(job.id),
        status=job.status,
        current_stage=stage.value,
        stage_label=stage.user_label,
        stage_index=stage.order,
        stage_total=STAGE_TOTAL,
        attempt=job.attempt,
        error_code=job.error_code,
        error_message=str(detail.get("message")) if detail.get("message") else None,
        retryable=job.retryable,
        degraded=list((job.stage_progress or {}).get("degraded", [])),
        kind=job.kind,
        section_key=job.section_key,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


async def latest_job(rt: Runtime, project_id: uuid.UUID) -> AnalysisJobRow:
    """The project's analysis job: a failed chapter regeneration never reads as a failed analysis."""
    async with rt.db.session() as s:
        job = (
            await s.execute(
                select(AnalysisJobRow)
                .where(AnalysisJobRow.project_id == project_id, AnalysisJobRow.kind == "analysis")
                .order_by(AnalysisJobRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    return job


async def latest_regeneration(rt: Runtime, project_id: uuid.UUID) -> AnalysisJobRow | None:
    async with rt.db.session() as s:
        return (
            await s.execute(
                select(AnalysisJobRow)
                .where(AnalysisJobRow.project_id == project_id, AnalysisJobRow.kind == "regenerate")
                .order_by(AnalysisJobRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def _enforce_creation_limits(request: Request, rt: Runtime, session: CurrentSession) -> None:
    cfg = rt.settings
    await enforce_rate_limit(rt, session, "projects", cfg.max_projects_per_hour, 3600)
    await enforce_ip_limits(
        rt, request, "projects", cfg.max_projects_per_ip_per_hour, cfg.max_projects_per_hour_global
    )


async def _create(
    request: Request,
    rt: Runtime,
    session: CurrentSession,
    *,
    input_type: InputType,
    locator: str | None,
    fingerprint: str,
    level: TechnicalLevel,
    instruction: str | None,
    name: str | None,
    upload: tuple[Path, str] | None = None,
) -> ProjectCreated:
    project_id = uuid.uuid4()
    upload_key: str | None = None
    if upload is not None:
        upload_key = f"projects/{project_id}/upload/source.zip"
        await rt.objects.put_file(upload_key, upload[0], "application/zip")
    async with rt.db.transaction() as s:
        sess = await s.get(SessionRow, session.id)
        assert sess is not None
        project = ProjectRow(
            id=project_id,
            session_id=session.id,
            input_type=input_type.value,
            source_locator=locator,
            source_fingerprint=fingerprint,
            detected_name=name,
            technical_level=level.value,
            user_instruction=(instruction or "").strip() or None,
            analysis_version=ANALYSIS_VERSION,
            upload_object_key=upload_key,
            expires_at=sess.created_at + timedelta(seconds=rt.settings.session_max_lifetime_seconds),
        )
        s.add(project)
        await s.flush()
        job = AnalysisJobRow(
            project_id=project_id,
            status=JobStatus.QUEUED.value,
            current_stage=JobStage.RECEIVED.value,
            stage_progress={},
            attempt=0,
        )
        s.add(job)
    # Publish before enqueueing so there is one writer at a time: concurrent publishers could commit
    # sequence ids out of order and a polling cursor would skip the earlier one.
    await rt.progress.publish(str(project_id), stage_event("stage", JobStage.RECEIVED))
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_analysis(str(job.id), 1)
    except UnfoldError as exc:
        # Committed but not enqueued: keep it `queued`; the sweeper re-enqueues within minutes.
        log.warning("project.enqueue_failed", project_id=str(project_id), code=exc.code.value)
    log.info("project.created", project_id=str(project_id), input_type=input_type.value)
    return ProjectCreated(project_id=str(project_id), job_id=str(job.id))


@router.post("/github", status_code=202, response_model=ProjectCreated)
async def create_github_project(
    body: CreateGithubProject, request: Request, rt: RuntimeDep, session: SessionDep
) -> ProjectCreated:
    ref = parse_github_url(body.url)  # syntax only; publicness is checked by the worker (no network here)
    await _enforce_creation_limits(request, rt, session)
    fingerprint = f"github:{ref.owner.lower()}/{ref.repo.lower()}@{ref.ref or ''}"
    return await _create(
        request,
        rt,
        session,
        input_type=InputType.GITHUB,
        locator=ref.canonical_url + (f"/tree/{ref.ref}" if ref.ref else ""),
        fingerprint=fingerprint,
        level=body.technical_level,
        instruction=body.instruction,
        name=ref.repo,
    )


@router.post("/zip", status_code=202, response_model=ProjectCreated)
async def create_zip_project(
    request: Request,
    rt: RuntimeDep,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    technical_level: Annotated[TechnicalLevel, Form()] = TechnicalLevel.INTERMEDIATE,
    instruction: Annotated[str | None, Form(max_length=1000)] = None,
) -> ProjectCreated:
    await _enforce_creation_limits(request, rt, session)
    limit = rt.settings.max_zip_bytes
    with tempfile.TemporaryDirectory(prefix="unfold-upload-") as tmp:
        dest = Path(tmp) / "upload.zip"
        digest = hashlib.sha256()
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise InputError(
                        f"The ZIP exceeds the {limit // (1024 * 1024)} MB limit.",
                        code=ErrorCode.INPUT_TOO_LARGE,
                        http_status=413,
                    )
                digest.update(chunk)
                out.write(chunk)
        with dest.open("rb") as fh:
            head = fh.read(4)
        if size == 0 or not any(head.startswith(m) for m in ZIP_MAGIC):
            raise InputError("The upload is not a ZIP archive.", code=ErrorCode.ZIP_INVALID)
        raw_name = Path(file.filename or "project.zip").name
        name = raw_name[:-4] if raw_name.lower().endswith(".zip") else raw_name
        return await _create(
            request,
            rt,
            session,
            input_type=InputType.ZIP,
            locator=raw_name[:200],
            fingerprint=f"zip:{digest.hexdigest()}",
            level=technical_level,
            instruction=instruction,
            name=name[:200] or "project",
            upload=(dest, raw_name),
        )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, rt: RuntimeDep, session: SessionDep) -> ProjectOut:
    project = await owned_project(rt, session, project_id)
    job = await latest_job(rt, project.id)
    return ProjectOut(
        id=str(project.id),
        name=project.detected_name,
        input_type=project.input_type,
        source_locator=project.source_locator,
        commit_sha=project.commit_sha,
        default_branch=project.default_branch,
        technical_level=project.technical_level,
        instruction=project.user_instruction,
        project_type=project.project_type,
        stats=project.stats,
        created_at=project.created_at,
        expires_at=project.expires_at,
        job=job_out(job),
    )


@router.delete("/{project_id}", status_code=202, response_model=dict[str, str])
async def delete_project(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> dict[str, str]:
    project = await owned_project(rt, session, project_id)
    # The marker is the durable part: from here every route 404s, and the sweeper finishes the cleanup even if
    # the enqueue below is lost (API restart, queue outage).
    await mark_deleting(rt, [project.id])
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_project_cleanup(str(project.id))
    except UnfoldError as exc:
        log.warning("project.cleanup_enqueue_failed", project_id=str(project.id), code=exc.code.value)
    return {"status": "deleting"}


@router.get("/{project_id}/progress", response_model=ProgressPage)
async def project_progress(
    project_id: str, rt: RuntimeDep, session: SessionDep, after: Annotated[int, Query(ge=0)] = 0
) -> ProgressPage:
    """Short-poll progress (HLD D10): events with `seq > after` plus the durable job state.

    Clients poll with `after` = the last `seq` they saw; every response also carries the job, so a client that
    missed events (or returns after a restart) still converges on the real state.
    """
    project = await owned_project(rt, session, project_id)
    events = await rt.progress.read_after(str(project.id), min(after, 2**63 - 1), limit=200)
    job = await latest_job(rt, project.id)
    regen = await latest_regeneration(rt, project.id)
    items = [ProgressItem(seq=seq, **event) for seq, event in events]
    return ProgressPage(
        events=items,
        cursor=events[-1][0] if events else after,
        job=job_out(job),
        regeneration=job_out(regen) if regen else None,
    )
