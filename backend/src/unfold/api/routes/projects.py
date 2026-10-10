"""Project creation, status, deletion and replayable progress (LLD §4 "Project creation", §20).

Creation commits project + job first, then enqueues (outbox-equivalent: the stale-job sweeper re-enqueues
`queued` jobs that never started, so a crash between commit and enqueue loses nothing).
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, File, Form, Request, UploadFile
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from unfold import ANALYSIS_VERSION
from unfold.api.deps import (
    CurrentSession,
    RuntimeDep,
    SessionDep,
    enforce_ip_limits,
    enforce_rate_limit,
    owned_project,
)
from unfold.api.schemas import CreateGithubProject, JobOut, ProjectCreated, ProjectOut
from unfold.api.streams import StreamCap
from unfold.core.errors import ErrorCode, InputError, RateLimited, UnfoldError
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
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


async def latest_job(rt: Runtime, project_id: uuid.UUID) -> AnalysisJobRow:
    async with rt.db.session() as s:
        job = (
            await s.execute(
                select(AnalysisJobRow)
                .where(AnalysisJobRow.project_id == project_id)
                .order_by(AnalysisJobRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    return job


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


@router.delete("/{project_id}", status_code=202)
async def delete_project(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> dict[str, str]:
    project = await owned_project(rt, session, project_id)
    queue: JobQueue = request.app.state.queue
    await queue.enqueue_project_cleanup(str(project.id))
    return {"status": "deleting"}


@router.get("/{project_id}/events")
async def project_events(
    project_id: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> EventSourceResponse:
    """Replayable SSE (HLD D10): replays the durable progress log after `Last-Event-ID`, then tails it.

    Superseded by short-poll `GET /projects/{id}/progress` in phase 2; capped per session until then (S-5).
    """
    project = await owned_project(rt, session, project_id)
    pid = str(project.id)
    raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id") or "0"
    start = min(int(raw_cursor), 2**63 - 1) if raw_cursor.isascii() and raw_cursor.isdigit() else 0
    caps: StreamCap = request.app.state.streams
    lease = caps.acquire(str(session.id))
    if lease is None:
        raise RateLimited("Too many open progress streams; try again shortly.")

    async def gen() -> AsyncIterator[dict[str, Any]]:
        try:
            cursor = start
            if cursor == 0:
                job = await latest_job(rt, project.id)
                stage = JobStage(job.current_stage)
                if job.status == JobStatus.SUCCEEDED.value:
                    yield {"event": "ready", "data": _dump(stage_event("ready", JobStage.READY))}
                    return
                if job.status == JobStatus.FAILED.value:
                    err = UnfoldError(
                        str((job.error_detail or {}).get("message", stage.user_label)),
                        code=ErrorCode(job.error_code or ErrorCode.INTERNAL_ERROR.value),
                        retryable=job.retryable,
                    )
                    yield {"event": "failed", "data": _dump(stage_event("failed", stage, error=err))}
                    return
            idle = 0
            while not await request.is_disconnected():
                events = await rt.progress.read_after(pid, cursor)
                if not events:
                    idle += 1
                    if idle % 10 == 0:
                        yield {
                            "event": "heartbeat",
                            "data": _dump(
                                {
                                    **stage_event("heartbeat", JobStage.RECEIVED),
                                    "stage": "",
                                    "label": "",
                                    "index": -1,
                                }
                            ),
                        }
                    if idle > 3600:  # an hour without progress: let the client reconnect
                        return
                    await asyncio.sleep(1)
                    continue
                idle = 0
                for seq, event in events:
                    cursor = seq
                    yield {"event": event["type"], "id": str(seq), "data": _dump(event)}
                    if event["type"] in ("ready", "failed"):
                        return
        finally:
            lease.release()

    # `background` also runs when the client vanishes before the generator is first iterated.
    return EventSourceResponse(
        gen(),
        ping=15,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(lease.release),
    )


def _dump(obj: dict[str, Any]) -> str:
    return orjson.dumps(obj).decode()
