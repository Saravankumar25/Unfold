"""Job progress: durable stage on the job row, then an ordered replayable event in the ProgressStore (LLD §5, HLD D10).

Progress reflects real stages only (PRD §7.2). A stage is published after its predecessor's writes committed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from unfold.core.errors import UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import PIPELINE, JobStage, JobStatus, check_transition
from unfold.domain.records import JobRec
from unfold.observability.tracing import tracer
from unfold.ports import ControlStore, ProgressStore

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
    """Job state transitions. Each is a mutator the store applies atomically (row lock or version check), so it
    must be a pure function of the record it is handed (it may be re-run after a lost race)."""

    def __init__(self, control: ControlStore, progress: ProgressStore, project_id: str, job_id: str) -> None:
        self.control = control
        self.progress = progress
        self.project_id = project_id
        self.job_id = job_id

    async def start(self, *, alive_within: timedelta) -> int | None:
        """Claim the job and mark it running (new attempt); the attempt number, or None when it is not ours.

        Done atomically so two workers started together cannot both own one job: a finished job, or a
        running one whose heartbeat is fresh, is left alone.
        """

        claimed = False

        def claim(job: JobRec) -> bool | None:
            nonlocal claimed
            claimed = False  # the store may run this again after a lost race
            now = datetime.now(UTC)
            if job.status == JobStatus.SUCCEEDED.value:
                return False
            if (
                job.status == JobStatus.RUNNING.value
                and job.heartbeat_at is not None
                and now - job.heartbeat_at < alive_within
            ):
                return False
            job.status = JobStatus.RUNNING.value
            job.attempt += 1
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.error_code = None
            job.error_detail = None
            claimed = True
            return True

        job = await self.control.update_job(self.job_id, claim)
        if not claimed:
            return None
        log.info("job.started", attempt=job.attempt)
        return job.attempt

    async def stage(self, stage: JobStage, detail: str | None = None) -> None:
        def apply(job: JobRec) -> bool | None:
            current = JobStage(job.current_stage)
            if not stage.is_failure and not current.is_terminal and 0 <= stage.order < current.order:
                return False  # resuming inside a group that already ran: keep the furthest stage reached
            check_transition(current, stage)
            job.current_stage = stage.value
            progress = dict(job.stage_progress or {})
            # a new nested dict: the stored document is replaced whole, never patched in place
            progress["stages"] = {**progress.get("stages", {}), stage.value: datetime.now(UTC).isoformat()}
            if detail:
                progress["detail"] = detail
            job.stage_progress = progress
            job.heartbeat_at = datetime.now(UTC)
            return True

        await self.control.update_job(self.job_id, apply)
        await self.progress.publish(self.project_id, stage_event("stage", stage, detail=detail))
        span = tracer.start_span(
            "job.stage", attributes={"stage": stage.value, "project_id": self.project_id}
        )
        span.end()
        log.info("job.stage", stage=stage.value, detail=detail)

    async def degraded(self, component: str) -> None:
        def apply(job: JobRec) -> bool | None:
            progress = dict(job.stage_progress or {})
            progress["degraded"] = sorted({*progress.get("degraded", []), component})
            job.stage_progress = progress
            return True

        await self.control.update_job(self.job_id, apply)

    async def heartbeat(self) -> None:
        def apply(job: JobRec) -> bool | None:
            job.heartbeat_at = datetime.now(UTC)
            return True

        await self.control.update_job(self.job_id, apply)

    async def succeed(self) -> None:
        def apply(job: JobRec) -> bool | None:
            check_transition(JobStage(job.current_stage), JobStage.READY)
            job.current_stage = JobStage.READY.value
            job.status = JobStatus.SUCCEEDED.value
            job.completed_at = datetime.now(UTC)
            job.retryable = False
            return True

        await self.control.update_job(self.job_id, apply)
        await self.progress.publish(self.project_id, stage_event("ready", JobStage.READY))
        log.info("job.ready")

    async def fail(self, error: UnfoldError, *, will_retry: bool) -> None:
        """Record a failure. When the runner will retry, the job stays `queued` at its current stage."""
        failure = JobStage(error.failure_stage)

        def apply(job: JobRec) -> bool | None:
            job.error_code = error.code.value
            job.error_detail = {"message": error.message, **{k: str(v) for k, v in error.detail.items()}}
            job.retryable = error.retryable
            if will_retry:
                job.status = JobStatus.QUEUED.value
            else:
                job.status = JobStatus.FAILED.value
                job.current_stage = failure.value
                job.completed_at = datetime.now(UTC)
            return True

        await self.control.update_job(self.job_id, apply)
        if not will_retry:
            await self.progress.publish(self.project_id, stage_event("failed", failure, error=error))
        log.warning("job.failed", code=error.code.value, will_retry=will_retry, stage=failure.value)
