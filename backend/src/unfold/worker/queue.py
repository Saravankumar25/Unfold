"""Job queue facade over arq (HLD D2). The api enqueues; the worker consumes. Queue names are fixed here."""

from __future__ import annotations

from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, StoreUnavailable

ANALYSIS_QUEUE = "unfold:analysis"
EXPORT_QUEUE = "unfold:export"
MAINTENANCE_QUEUE = "unfold:maintenance"


def redis_settings(settings: Settings) -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


class JobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, settings: Settings) -> JobQueue:
        return cls(await create_pool(redis_settings(settings)))

    async def aclose(self) -> None:
        await self._pool.aclose()

    async def _enqueue(
        self, fn: str, queue: str, *args: Any, job_key: str, defer_s: float | None = None
    ) -> None:
        try:
            await self._pool.enqueue_job(fn, *args, _queue_name=queue, _job_id=job_key, _defer_by=defer_s)
        except OSError as exc:
            raise StoreUnavailable("The job queue is unavailable.", code=ErrorCode.QUEUE_UNAVAILABLE) from exc

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        # One arq job per (analysis job, attempt): duplicates from the sweeper are dropped by arq.
        await self._enqueue(
            "run_analysis", ANALYSIS_QUEUE, job_id, job_key=f"analysis:{job_id}:{attempt}", defer_s=defer_s
        )

    async def enqueue_export(self, artifact_id: str) -> None:
        await self._enqueue("run_export", EXPORT_QUEUE, artifact_id, job_key=f"export:{artifact_id}")

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        await self._enqueue("cleanup_project", MAINTENANCE_QUEUE, project_id, job_key=f"cleanup:{project_id}")
