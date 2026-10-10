"""The sweeper (LLD §21): one idempotent function a scheduler, the API process or `worker.run sweep` can call.

Each pass moves expired sessions and projects to `deleting`, finishes every `deleting` project (all stores, then the
record), removes sessions that have nothing left (plus their rate-limit keys), restarts stale analysis jobs and
exports, and drops expired export artifacts. Running it twice in a row does nothing the second time; a crash
anywhere leaves only work the next pass completes. Works on whichever `ControlStore` the runtime holds; the
AWS sweeper Lambda runs the same function (plus the task-level work only it can do).
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from unfold.cleanup.service import cleanup_project, mark_deleting
from unfold.core.logging import get_logger
from unfold.domain.records import SessionRec
from unfold.ports import SESSION_RATE_BUCKETS, JobQueue, session_rate_key
from unfold.runtime import Runtime
from unfold.worker.recovery import requeue_incomplete_jobs

log = get_logger(__name__)

EXPORT_STALE_AFTER = timedelta(minutes=5)
BATCH = 500  # per kind and pass; the scheduled AWS sweeper passes a smaller one (Lambda time bound)


@dataclass
class SweepReport:
    marked_deleting: int = 0
    projects_cleaned: int = 0
    projects_deferred: int = 0
    projects_failed: int = 0
    sessions_removed: int = 0
    rate_limit_keys: int = 0
    jobs_requeued: int = 0
    exports_requeued: int = 0
    exports_expired: int = 0
    steps_failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def sweep(
    rt: Runtime,
    queue: JobQueue | None = None,
    *,
    now: datetime | None = None,
    queued_for: timedelta = timedelta(0),
    batch: int = BATCH,
) -> SweepReport:
    """Without a `queue` the jobs and exports that need restarting are left for a pass that has one.
    `queued_for` is how long a job must have waited before it counts as lost (outbox reconciliation); `batch`
    bounds the work per kind in one pass, the next pass continues."""
    now = now or datetime.now(UTC)
    report = SweepReport()
    control = rt.control

    expired: list[SessionRec] = await control.expired_sessions(now, batch)
    doomed: list[str] = [p.id for p in await control.expired_projects(now, batch)]
    for session in expired:
        if session.status != "expired":

            def expire(row: SessionRec) -> bool:
                row.status = "expired"
                return True

            await control.update_session(session.token_hash, expire)
        doomed += [p.id for p, _ in await control.projects_of_session(session.id)]
    report.marked_deleting = await mark_deleting(rt, doomed)

    for project in await control.deleting_projects(batch):
        try:
            counts = await cleanup_project(rt, project.id)
        except Exception as exc:  # one broken project must not stop the rest; its marker keeps it queued
            report.projects_failed += 1
            log.error("sweep.cleanup_failed", project_id=project.id, error_type=type(exc).__name__)
            continue
        if counts.get("deferred"):
            report.projects_deferred += 1
        else:
            report.projects_cleaned += 1

    # Each step is isolated: a store that is down for one of them must not starve the others.
    await _step(report, "sessions", _remove_empty_sessions(rt, expired, report))
    if queue is not None:
        requeue = requeue_incomplete_jobs(rt, queue, now=now, queued_for=queued_for)
        report.jobs_requeued = await _step(report, "jobs", requeue) or 0
        report.exports_requeued = (
            await _step(report, "exports", restart_stale_exports(rt, queue, now, batch)) or 0
        )
    report.exports_expired = await _step(report, "expiry", _expire_exports(rt, now, batch)) or 0
    log.info("sweep.done", **report.as_dict())
    return report


async def _step[T](report: SweepReport, name: str, work: Coroutine[Any, Any, T]) -> T | None:
    try:
        return await work
    except Exception as exc:
        report.steps_failed += 1
        log.error("sweep.step_failed", step=name, error_type=type(exc).__name__)
        return None


async def _remove_empty_sessions(rt: Runtime, sessions: list[SessionRec], report: SweepReport) -> None:
    for session in sessions:
        if await rt.control.projects_of_session(session.id):
            continue  # a deferred project still hangs on it; the next pass removes both
        for bucket in SESSION_RATE_BUCKETS:
            report.rate_limit_keys += await rt.limiter.delete_prefix(session_rate_key(bucket, session.id))
        await rt.control.delete_session(session.token_hash)
        report.sessions_removed += 1


async def restart_stale_exports(rt: Runtime, queue: JobQueue, now: datetime, batch: int = BATCH) -> int:
    """Pending exports whose enqueue was lost (or whose worker died) are sent again; the job is idempotent."""
    stale = await rt.control.stale_pending_artifacts(
        created_before=now - EXPORT_STALE_AFTER, now=now, limit=batch
    )
    restarted = 0
    for art in stale:
        try:
            await queue.enqueue_export(art.id)
            restarted += 1
        except Exception as exc:  # one failing artifact must not starve the rest
            log.error("sweep.export_restart_failed", export_id=art.id, error_type=type(exc).__name__)
    return restarted


async def _expire_exports(rt: Runtime, now: datetime, batch: int) -> int:
    expired = 0
    for art in await rt.control.expired_artifacts(now, batch):
        try:
            if art.object_key:
                await rt.objects.delete_prefix(art.object_key)
            await rt.control.delete_artifact(art.id)
            expired += 1
        except Exception as exc:  # the record stays, so the next pass retries it
            log.error("sweep.export_expire_failed", export_id=art.id, error_type=type(exc).__name__)
    return expired
