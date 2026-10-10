"""Project creation, status, deletion and replayable progress (LLD §4 "Project creation", §20).

Creation commits project + job first, then enqueues (outbox-equivalent: the sweeper re-enqueues `queued` jobs
that never started, so a crash between commit and enqueue loses nothing). ZIPs reach the bucket by presigned POST
(`upload-url`, then `complete`); the multipart route stays for the local stack (ADR 0014).
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

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
    CreateZipUpload,
    JobOut,
    ProgressItem,
    ProgressPage,
    ProjectCreated,
    ProjectOut,
    UploadTicket,
)
from unfold.core.errors import Conflict, ErrorCode, InputError, NotFound, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import InputType, JobStage, JobStatus, TechnicalLevel
from unfold.domain.records import JobRec, ProjectRec
from unfold.ingestion.archive import ZIP_MAGIC
from unfold.ingestion.github import parse_github_url
from unfold.ingestion.uploads import inspect_upload
from unfold.orchestration.progress import STAGE_TOTAL, stage_event
from unfold.ports import JobQueue, UploadStore
from unfold.runtime import Runtime

router = APIRouter(prefix="/projects", tags=["projects"])
log = get_logger(__name__)


def job_out(job: JobRec) -> JobOut:
    stage = JobStage(job.current_stage)
    detail = job.error_detail or {}
    return JobOut(
        id=job.id,
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
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


async def latest_job(rt: Runtime, project_id: str) -> JobRec:
    job = await rt.control.latest_job(project_id)
    if job is None:  # a presigned upload that was never completed
        raise Conflict("The upload for this project was not completed.", code=ErrorCode.PROJECT_NOT_READY)
    return job


async def _enforce_creation_limits(request: Request, rt: Runtime, session: CurrentSession) -> None:
    cfg = rt.settings
    await enforce_rate_limit(rt, session, "projects", cfg.max_projects_per_hour, 3600)
    await enforce_ip_limits(
        rt, request, "projects", cfg.max_projects_per_ip_per_hour, cfg.max_projects_per_hour_global
    )


def _new_project(
    rt: Runtime,
    session: CurrentSession,
    *,
    input_type: InputType,
    locator: str | None,
    fingerprint: str,
    level: TechnicalLevel,
    instruction: str | None,
    name: str | None,
    expires_at: datetime | None = None,
) -> ProjectRec:
    lifetime = timedelta(seconds=rt.settings.session_max_lifetime_seconds)
    return ProjectRec(
        id=str(uuid.uuid4()),
        session_id=session.id,
        input_type=input_type.value,
        source_locator=locator,
        source_fingerprint=fingerprint,
        detected_name=name,
        technical_level=level.value,
        user_instruction=(instruction or "").strip() or None,
        analysis_version=ANALYSIS_VERSION,
        created_at=datetime.now(UTC),
        expires_at=expires_at or session.created_at + lifetime,
    )


def _new_job(project_id: str) -> JobRec:
    return JobRec(
        id=str(uuid.uuid4()),
        project_id=project_id,
        created_at=datetime.now(UTC),
        status=JobStatus.QUEUED.value,
        current_stage=JobStage.RECEIVED.value,
    )


async def _enqueue(request: Request, rt: Runtime, project_id: str, job_id: str) -> ProjectCreated:
    # Publish before enqueueing so there is one writer at a time: concurrent publishers could commit
    # sequence ids out of order and a polling cursor would skip the earlier one.
    await rt.progress.publish(project_id, stage_event("stage", JobStage.RECEIVED))
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_analysis(job_id, 1)
    except UnfoldError as exc:
        # Committed but not enqueued: keep it `queued`; the sweeper re-enqueues within minutes.
        log.warning("project.enqueue_failed", project_id=project_id, code=exc.code.value)
    return ProjectCreated(project_id=project_id, job_id=job_id)


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
    project = _new_project(
        rt,
        session,
        input_type=input_type,
        locator=locator,
        fingerprint=fingerprint,
        level=level,
        instruction=instruction,
        name=name,
    )
    if upload is not None:
        project.upload_object_key = f"projects/{project.id}/upload/source.zip"
        await rt.objects.put_file(project.upload_object_key, upload[0], "application/zip")
    job = _new_job(project.id)
    cfg = rt.settings
    await rt.control.create_project(
        project,
        job,
        max_active_per_session=cfg.max_active_jobs_per_session,
        max_queued_global=cfg.max_queued_jobs_global,
    )
    created = await _enqueue(request, rt, project.id, job.id)
    log.info("project.created", project_id=project.id, input_type=input_type.value)
    return created


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
    if not rt.settings.allow_direct_upload:
        raise NotFound("Direct upload is not available here; request an upload URL instead.")
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


@router.post("/zip/upload-url", status_code=201, response_model=UploadTicket)
async def create_zip_upload_url(
    body: CreateZipUpload, request: Request, rt: RuntimeDep, session: SessionDep
) -> UploadTicket:
    """Step 1 of a browser-direct upload: register the project and return a presigned POST (ADR 0014).

    The key is derived here from the new project id (never from the client) and the policy pins key, type and
    size, so the browser cannot write anywhere else or send more than the configured limit.
    """
    cfg = rt.settings
    store = rt.objects
    if not isinstance(store, UploadStore):
        raise NotFound("Browser uploads are not available in this deployment.")
    limit = cfg.max_zip_bytes
    if body.size > limit:
        raise InputError(
            f"The ZIP exceeds the {limit // (1024 * 1024)} MB limit.",
            code=ErrorCode.INPUT_TOO_LARGE,
            http_status=413,
        )
    await _enforce_creation_limits(request, rt, session)
    await enforce_rate_limit(rt, session, "upload_urls", cfg.max_upload_urls_per_session_per_hour, 3600)
    raw_name = Path(body.filename).name
    name = raw_name[:-4] if raw_name.lower().endswith(".zip") else raw_name
    ttl = cfg.upload_url_ttl_seconds
    project = _new_project(
        rt,
        session,
        input_type=InputType.ZIP,
        locator=raw_name[:200],
        fingerprint="zip:pending",
        level=body.technical_level,
        instruction=body.instruction,
        name=name[:200] or "project",
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl * 3),  # abandoned uploads are swept soon
    )
    project.upload_object_key = f"projects/{project.id}/upload/source.zip"
    await rt.control.create_project(project, None)
    post = await store.presign_upload(
        project.upload_object_key, max_bytes=limit, content_type="application/zip", expires_s=ttl
    )
    return UploadTicket(
        project_id=project.id,
        url=post.url,
        fields=post.fields,
        max_bytes=post.max_bytes,
        expires_at=post.expires_at,
    )


@router.post("/{project_id}/zip/complete", status_code=202, response_model=ProjectCreated)
async def complete_zip_upload(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> ProjectCreated:
    """Step 2: the object exists; inspect it, then create the job. Safe to call twice (returns the same job)."""
    project = await owned_project(rt, session, project_id)
    store = rt.objects
    if project.input_type != InputType.ZIP.value or not project.upload_object_key:
        raise Conflict("This project has no pending upload.")
    existing = await rt.control.latest_job(project.id)
    if existing is not None:
        return ProjectCreated(project_id=project.id, job_id=existing.id)
    if not isinstance(store, UploadStore):
        raise NotFound("Browser uploads are not available in this deployment.")
    size = await inspect_upload(store, project.upload_object_key, rt.settings.max_zip_bytes)
    lifetime = timedelta(seconds=rt.settings.session_max_lifetime_seconds)

    def activate(row: ProjectRec) -> None:
        row.source_fingerprint = f"zip:size={size}"
        row.expires_at = session.created_at + lifetime

    await rt.control.update_project(project.id, activate)
    job = _new_job(project.id)
    cfg = rt.settings
    try:
        await rt.control.add_job(
            job,
            max_active_per_session=cfg.max_active_jobs_per_session,
            max_queued_global=cfg.max_queued_jobs_global,
        )
    except Conflict:  # a concurrent complete won: one project, one job
        winner = await rt.control.latest_job(project.id)
        if winner is None:
            raise
        return ProjectCreated(project_id=project.id, job_id=winner.id)
    created = await _enqueue(request, rt, project.id, job.id)
    log.info("project.created", project_id=project.id, input_type="zip", size=size)
    return created


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, rt: RuntimeDep, session: SessionDep) -> ProjectOut:
    project = await owned_project(rt, session, project_id)
    job = await latest_job(rt, project.id)
    return ProjectOut(
        id=project.id,
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


@router.delete("/{project_id}", status_code=202)
async def delete_project(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> dict[str, str]:
    project = await owned_project(rt, session, project_id)

    def tombstone(row: ProjectRec) -> bool:
        # hidden from every read at once; the sweeper finishes the deletion if the cleanup message is lost
        row.expires_at = datetime.now(UTC)
        return True

    await rt.control.update_project(project.id, tombstone)
    queue: JobQueue = request.app.state.queue
    await queue.enqueue_project_cleanup(project.id)
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
    events = await rt.progress.read_after(project.id, min(after, 2**63 - 1), limit=200)
    job = await latest_job(rt, project.id)
    items = [ProgressItem(seq=seq, **event) for seq, event in events]
    return ProgressPage(events=items, cursor=events[-1][0] if events else after, job=job_out(job))
