"""The sweeper (LLD §21): one idempotent function a scheduler, the API process or `worker.run sweep` can call.

Each pass moves expired sessions and projects to `deleting`, finishes every `deleting` project (all stores, then the
row), removes sessions that have nothing left (plus their rate-limit keys), restarts stale analysis jobs and
exports, and drops expired export artifacts. Running it twice in a row does nothing the second time; a crash
anywhere leaves only work the next pass completes.
"""

from __future__ import annotations

import uuid
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from unfold.cleanup.service import cleanup_project, mark_deleting
from unfold.core.logging import get_logger
from unfold.domain.enums import ArtifactStatus
from unfold.persistence.postgres.models import ArtifactRow, ProjectRow, SessionRow
from unfold.ports import SESSION_RATE_BUCKETS, JobQueue, session_rate_key
from unfold.runtime import Runtime
from unfold.worker.recovery import requeue_incomplete_jobs

log = get_logger(__name__)

EXPORT_STALE_AFTER = timedelta(minutes=5)


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


async def sweep(rt: Runtime, queue: JobQueue | None = None, *, now: datetime | None = None) -> SweepReport:
    """Without a `queue` the jobs and exports that need restarting are left for a pass that has one."""
    now = now or datetime.now(UTC)
    report = SweepReport()

    async with rt.db.transaction() as s:
        expired_sessions = list(
            (await s.execute(select(SessionRow.id).where(SessionRow.expires_at <= now))).scalars()
        )
        await s.execute(
            update(SessionRow).where(SessionRow.id.in_(expired_sessions)).values(status="expired")
        )
        doomed = list(
            (
                await s.execute(
                    select(ProjectRow.id).where(
                        ProjectRow.deleting_at.is_(None),
                        (ProjectRow.expires_at <= now) | ProjectRow.session_id.in_(expired_sessions),
                    )
                )
            ).scalars()
        )
    report.marked_deleting = await mark_deleting(rt, doomed)

    async with rt.db.session() as s:
        deleting = list(
            (await s.execute(select(ProjectRow.id).where(ProjectRow.deleting_at.is_not(None)))).scalars()
        )
    for pid in deleting:
        try:
            counts = await cleanup_project(rt, str(pid))
        except Exception as exc:  # one broken project must not stop the rest; its marker keeps it queued
            report.projects_failed += 1
            log.error("sweep.cleanup_failed", project_id=str(pid), error_type=type(exc).__name__)
            continue
        if counts.get("deferred"):
            report.projects_deferred += 1
        else:
            report.projects_cleaned += 1

    # Each step is isolated: a store that is down for one of them must not starve the others.
    await _step(report, "sessions", _remove_empty_sessions(rt, expired_sessions, report))
    if queue is not None:
        report.jobs_requeued = await _step(report, "jobs", requeue_incomplete_jobs(rt, queue, now=now)) or 0
        report.exports_requeued = await _step(report, "exports", _restart_stale_exports(rt, queue, now)) or 0
    report.exports_expired = await _step(report, "expiry", _expire_exports(rt, now)) or 0
    log.info("sweep.done", **report.as_dict())
    return report


async def _step[T](report: SweepReport, name: str, work: Coroutine[Any, Any, T]) -> T | None:
    try:
        return await work
    except Exception as exc:
        report.steps_failed += 1
        log.error("sweep.step_failed", step=name, error_type=type(exc).__name__)
        return None


async def _remove_empty_sessions(rt: Runtime, session_ids: list[uuid.UUID], report: SweepReport) -> None:
    if not session_ids:
        return
    async with rt.db.session() as s:
        busy = set(
            (
                await s.execute(
                    select(ProjectRow.session_id).where(ProjectRow.session_id.in_(session_ids)).distinct()
                )
            ).scalars()
        )
    for sid in session_ids:
        if sid in busy:
            continue  # a deferred project still hangs on it; the next pass removes both
        for bucket in SESSION_RATE_BUCKETS:
            report.rate_limit_keys += await rt.limiter.delete_prefix(session_rate_key(bucket, sid))
        async with rt.db.transaction() as s:
            result = await s.execute(delete(SessionRow).where(SessionRow.id == sid))
            report.sessions_removed += int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _restart_stale_exports(rt: Runtime, queue: JobQueue, now: datetime) -> int:
    async with rt.db.session() as s:
        ids = (
            (
                await s.execute(
                    select(ArtifactRow.id)
                    .join(ProjectRow, ProjectRow.id == ArtifactRow.project_id)
                    .where(
                        ArtifactRow.status == ArtifactStatus.PENDING.value,
                        ArtifactRow.created_at <= now - EXPORT_STALE_AFTER,
                        ArtifactRow.expires_at > now,
                        ProjectRow.deleting_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    restarted = 0
    for artifact_id in ids:
        try:
            await queue.enqueue_export(str(artifact_id))
            restarted += 1
        except Exception as exc:  # one failing artifact must not starve the rest
            log.error(
                "sweep.export_restart_failed", export_id=str(artifact_id), error_type=type(exc).__name__
            )
    return restarted


async def _expire_exports(rt: Runtime, now: datetime) -> int:
    async with rt.db.session() as s:
        rows = (
            await s.execute(
                select(ArtifactRow.id, ArtifactRow.object_key).where(ArtifactRow.expires_at <= now)
            )
        ).all()
    expired = 0
    for artifact_id, key in rows:
        try:
            if key:
                await rt.objects.delete_prefix(key)
            async with rt.db.transaction() as s:
                await s.execute(delete(ArtifactRow).where(ArtifactRow.id == artifact_id))
            expired += 1
        except Exception as exc:  # the row stays, so the next pass retries it
            log.error("sweep.export_expire_failed", export_id=str(artifact_id), error_type=type(exc).__name__)
    return expired
