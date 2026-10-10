"""SubprocessJobQueue never supervises one job twice (JobQueue port contract); no worker process is started."""

from __future__ import annotations

import asyncio

import pytest

from unfold.core.config import Settings
from unfold.worker.queue import SubprocessJobQueue


async def test_same_job_enqueued_twice_is_supervised_once(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = SubprocessJobQueue(Settings())
    started: list[tuple[str, str]] = []
    release = asyncio.Event()

    async def fake_supervise(task: str, ref: str, delay: float) -> None:
        started.append((task, ref))
        await release.wait()

    monkeypatch.setattr(queue, "_supervise", fake_supervise)
    await queue.enqueue_analysis("job-1", 1)
    await queue.enqueue_analysis("job-1", 2)  # a different attempt of the same job is still the same job
    await queue.enqueue_analysis("job-2", 1)
    await asyncio.sleep(0)
    assert sorted(started) == [("analysis", "job-1"), ("analysis", "job-2")]

    release.set()
    await asyncio.gather(*queue._tasks.values())
    await asyncio.sleep(0)
    await queue.enqueue_analysis("job-1", 3)  # finished jobs may be enqueued again
    await asyncio.sleep(0)
    assert started.count(("analysis", "job-1")) == 2
    await queue.aclose()
