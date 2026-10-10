"""Re-enqueue jobs whose worker is gone (API restart, crashed host). Called at API start; phase 3 schedules it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import JobStatus
from unfold.orchestration.progress import ProgressReporter
from unfold.persistence.postgres.models import AnalysisJobRow
from unfold.ports import JobQueue
from unfold.runtime import Runtime

log = get_logger(__name__)

STALE_AFTER = timedelta(minutes=5)


async def requeue_incomplete_jobs(rt: Runtime, queue: JobQueue, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    stale_running = (AnalysisJobRow.status == "running") & (
        AnalysisJobRow.heartbeat_at.is_(None) | (AnalysisJobRow.heartbeat_at < cutoff)
    )
    async with rt.db.session() as s:
        rows = (
            await s.execute(
                select(AnalysisJobRow.id, AnalysisJobRow.attempt).where(
                    or_(AnalysisJobRow.status == "queued", stale_running)
                )
            )
        ).all()
    for job_id, attempt in rows:
        await queue.enqueue_analysis(str(job_id), int(attempt) + 1)
    if rows:
        log.info("jobs.requeued", count=len(rows))
    return len(rows)


async def fail_abandoned_job(rt: Runtime, job_id: str) -> None:
    """The supervisor gave up on a job whose worker kept dying: record the failure so polling terminates."""
    async with rt.db.session() as s:
        job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
    if job is None or job.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
        return
    err = UnfoldError(
        "The analysis worker stopped repeatedly and the job was abandoned.",
        code=ErrorCode.ANALYSIS_STAGE_FAILED,
        retryable=False,
    )
    await ProgressReporter(rt.db, rt.progress, str(job.project_id), job_id).fail(err, will_retry=False)
