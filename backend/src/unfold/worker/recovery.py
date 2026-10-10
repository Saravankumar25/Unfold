"""Re-enqueue jobs whose worker is gone (API restart, crashed host). One step of `cleanup.sweeper.sweep`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import JobStatus
from unfold.orchestration.progress import ProgressReporter
from unfold.ports import JobQueue
from unfold.runtime import Runtime

log = get_logger(__name__)

STALE_AFTER = timedelta(minutes=5)


async def requeue_incomplete_jobs(
    rt: Runtime,
    queue: JobQueue,
    *,
    now: datetime | None = None,
    queued_for: timedelta = timedelta(0),
    limit: int = 100,
) -> int:
    """Re-send jobs that are `queued` for at least `queued_for`, or `running` without a recent heartbeat.

    Idempotent: a duplicate message is harmless because starting a job is a conditional claim. This is the
    outbox reconciliation: a job committed but never enqueued (crash between the two) is found here.
    """
    t = now or datetime.now(UTC)
    jobs = await rt.control.recoverable_jobs(
        queued_before=t - queued_for, heartbeat_before=t - STALE_AFTER, limit=limit
    )
    for job in jobs:
        await queue.enqueue_analysis(job.id, job.attempt + 1)
    if jobs:
        log.info("jobs.requeued", count=len(jobs))
    return len(jobs)


async def fail_abandoned_job(
    rt: Runtime,
    job_id: str,
    message: str = "The analysis worker stopped repeatedly and the job was abandoned.",
) -> None:
    """A job nobody can run (its worker keeps dying, or its message was dead-lettered): record the failure so
    polling terminates instead of waiting for work that will never happen."""
    job = await rt.control.get_job(job_id)
    if job is None or job.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
        return
    err = UnfoldError(message, code=ErrorCode.ANALYSIS_STAGE_FAILED, retryable=False)
    await ProgressReporter(rt.control, rt.progress, job.project_id, job_id).fail(err, will_retry=False)
