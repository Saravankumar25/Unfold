"""Job progress: durable stage on the job row, then an ordered replayable event in the ProgressStore (LLD §5, HLD D10).

Progress reflects real stages only (PRD §7.2). A stage is published after its predecessor's writes committed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unfold.core.errors import UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import PIPELINE, JobStage, JobStatus, check_transition
from unfold.observability.tracing import tracer
from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.models import AnalysisJobRow
from unfold.ports import ProgressStore

log = get_logger(__name__)
STAGE_TOTAL = len(PIPELINE)


def stage_event(
    event_type: str, stage: JobStage, *, detail: str | None = None, error: UnfoldError | None = None
) -> dict[str, Any]:
    """Build a `ProgressEvent` payload (api.schemas.ProgressEvent)."""
    return {
        "type": event_type,
        "stage": stage.value,
        "label": stage.user_label,
        "index": stage.order,
        "total": STAGE_TOTAL,
        "detail": detail,
        "error_code": error.code.value if error else None,
        "message": error.message if error else None,
        "retryable": error.retryable if error else None,
        "at": datetime.now(UTC).isoformat(),
    }


class ProgressReporter:
    def __init__(self, db: Database, progress: ProgressStore, project_id: str, job_id: str) -> None:
        self.db = db
        self.progress = progress
        self.project_id = project_id
        self.job_id = uuid.UUID(job_id)

    async def _load(self, session: AsyncSession) -> AnalysisJobRow:
        stmt = select(AnalysisJobRow).where(AnalysisJobRow.id == self.job_id).with_for_update()
        row: AnalysisJobRow = (await session.execute(stmt)).scalar_one()
        return row

    async def start(self, *, alive_within: timedelta) -> int | None:
        """Claim the job and mark it running (new attempt); the attempt number, or None when it is not ours.

        Done under the row lock so two workers started together cannot both own one job: a finished job, or a
        running one whose heartbeat is fresh, is left alone.
        """
        async with self.db.transaction() as s:
            row = await self._load(s)
            if row.status == JobStatus.SUCCEEDED.value:
                return None
            if (
                row.status == JobStatus.RUNNING.value
                and row.heartbeat_at is not None
                and datetime.now(UTC) - row.heartbeat_at < alive_within
            ):
                return None
            row.status = JobStatus.RUNNING.value
            row.attempt += 1
            row.started_at = row.started_at or datetime.now(UTC)
            row.heartbeat_at = datetime.now(UTC)
            row.error_code = None
            row.error_detail = None
            attempt = row.attempt
        log.info("job.started", attempt=attempt)
        return attempt

    async def stage(self, stage: JobStage, detail: str | None = None) -> None:
        async with self.db.transaction() as s:
            row = await self._load(s)
            current = JobStage(row.current_stage)
            if not stage.is_failure and not current.is_terminal and 0 <= stage.order < current.order:
                return  # resuming inside a group that already ran: keep the furthest stage reached
            check_transition(current, stage)
            row.current_stage = stage.value
            progress = dict(row.stage_progress or {})
            # a new nested dict: mutating the shared one is invisible to SQLAlchemy (plain JSONB column)
            progress["stages"] = {**progress.get("stages", {}), stage.value: datetime.now(UTC).isoformat()}
            if detail:
                progress["detail"] = detail
            row.stage_progress = progress
            row.heartbeat_at = datetime.now(UTC)
        await self.progress.publish(self.project_id, stage_event("stage", stage, detail=detail))
        span = tracer.start_span(
            "job.stage", attributes={"stage": stage.value, "project_id": self.project_id}
        )
        span.end()
        log.info("job.stage", stage=stage.value, detail=detail)

    async def degraded(self, component: str) -> None:
        async with self.db.transaction() as s:
            row = await self._load(s)
            progress = dict(row.stage_progress or {})
            deg = set(progress.get("degraded", []))
            deg.add(component)
            progress["degraded"] = sorted(deg)
            row.stage_progress = progress

    async def heartbeat(self) -> None:
        async with self.db.transaction() as s:
            row = await self._load(s)
            row.heartbeat_at = datetime.now(UTC)

    async def succeed(self) -> None:
        async with self.db.transaction() as s:
            row = await self._load(s)
            check_transition(JobStage(row.current_stage), JobStage.READY)
            row.current_stage = JobStage.READY.value
            row.status = JobStatus.SUCCEEDED.value
            row.completed_at = datetime.now(UTC)
            row.retryable = False
        await self.progress.publish(self.project_id, stage_event("ready", JobStage.READY))
        log.info("job.ready")

    async def fail(self, error: UnfoldError, *, will_retry: bool) -> None:
        """Record a failure. When the runner will retry, the job stays `queued` at its current stage."""
        failure = JobStage(error.failure_stage)
        async with self.db.transaction() as s:
            row = await self._load(s)
            row.error_code = error.code.value
            row.error_detail = {"message": error.message, **{k: str(v) for k, v in error.detail.items()}}
            row.retryable = error.retryable
            if will_retry:
                row.status = JobStatus.QUEUED.value
            else:
                row.status = JobStatus.FAILED.value
                row.current_stage = failure.value
                row.completed_at = datetime.now(UTC)
        if not will_retry:
            await self.progress.publish(self.project_id, stage_event("failed", failure, error=error))
        log.warning("job.failed", code=error.code.value, will_retry=will_retry, stage=failure.value)
