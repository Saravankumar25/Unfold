"""`JobQueue` adapter for local runs (HLD D2): one one-shot worker process per job.

`python -m unfold.worker.run analysis <job_id>` is exactly what a Fargate task runs in the AWS adapter; only the
launcher differs. No long-lived worker loop exists. The job row in Postgres is the durable queue; this adapter just
starts processes (bounded by `MAX_CONCURRENT_JOBS`) and restarts a job whose worker asked to be retried.
"""

from __future__ import annotations

import asyncio
import sys

from unfold.core.config import Settings
from unfold.core.logging import get_logger
from unfold.orchestration.pipeline import RETRY_EXIT_CODE
from unfold.ports import JobQueue

log = get_logger(__name__)


class SubprocessJobQueue(JobQueue):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._slots = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @classmethod
    async def connect(cls, settings: Settings) -> SubprocessJobQueue:
        return cls(settings)

    async def aclose(self) -> None:
        """Stop supervising. Running workers are separate processes and finish on their own."""
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        self._spawn(f"analysis:{job_id}", "analysis", job_id, defer_s or 0.0)

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        self._spawn(f"cleanup:{project_id}", "cleanup", project_id, 0.0)

    def _spawn(self, key: str, task: str, ref: str, delay: float) -> None:
        if key in self._tasks and not self._tasks[key].done():
            return  # idempotent: the same job is never supervised twice
        self._tasks[key] = asyncio.create_task(self._supervise(task, ref, delay), name=key)

    async def _supervise(self, task: str, ref: str, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        async with self._slots:
            for attempt in range(1, self._settings.max_job_attempts + 1):
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "unfold.worker.run", task, ref
                )
                code = await proc.wait()
                log.info("worker.exited", task=task, ref=ref, code=code, attempt=attempt)
                if code != RETRY_EXIT_CODE:
                    return
                await asyncio.sleep(min(30.0, 5.0 * attempt))
