"""Re-enqueue jobs whose worker is gone (API restart, crashed host). Called at API start; phase 3 schedules it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from unfold.core.logging import get_logger
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
