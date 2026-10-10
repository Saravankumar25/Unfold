"""Job queue selection by profile (composition-root helper; imports the adapter only when it is chosen)."""

from __future__ import annotations

from unfold.core.config import Settings
from unfold.ports import JobQueue


async def build_job_queue(settings: Settings) -> JobQueue:
    if settings.persistence_backend == "dynamodb":
        from unfold.worker.sqs_queue import SqsJobQueue

        return SqsJobQueue(settings)
    from unfold.worker.queue import SubprocessJobQueue

    return await SubprocessJobQueue.connect(settings)
