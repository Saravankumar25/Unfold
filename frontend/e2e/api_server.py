"""E2E API server: the real FastAPI app on the real stores (docker compose), with the scripted model.

Test tooling, not a production path. `create_app(settings, queue=..., llm=...)` is the documented injection point;
this launcher supplies an in-process queue (`unfold.worker.run`'s work, one thread per job) and `ScriptedLLM`, so the
journeys exercise parsing, indexing, graph, retrieval, verification, PDF rendering and every API route without
spending model quota. Isolation matches the integration tests: database `<db>_test`, Redis DB 1, bucket
`unfold-test`, Qdrant prefix `unfold_test`. Run from `backend/`: `uv run python ../frontend/e2e/api_server.py`.
Jobs of GitHub projects are left queued on purpose: ingestion would need the network.
`E2E_LIVE=1` instead uses the real gateway, the subprocess queue and GitHub (the on-demand live smoke).
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections.abc import Awaitable, Callable

import uvicorn

from e2e_env import e2e_settings, prepare_database
from unfold.api.main import create_app
from unfold.cleanup.service import cleanup_project
from unfold.core.config import Settings
from unfold.core.errors import ExportError
from unfold.evaluation.scripted import ScriptedLLM
from unfold.exports.pdf import PdfRenderer
from unfold.exports.service import mark_failed, run_export
from unfold.orchestration.pipeline import run_analysis
from unfold.persistence.postgres.models import AnalysisJobRow, ProjectRow
from unfold.ports import JobQueue
from unfold.runtime import Runtime

PORT = int(os.environ.get("E2E_API_PORT", "8010"))
LIVE = os.environ.get("E2E_LIVE") == "1"  # real gateway, worker subprocesses and GitHub: the on-demand smoke


class InProcessQueue(JobQueue):
    """`JobQueue` that runs what `python -m unfold.worker.run` would, each job on its own thread and event loop.

    A thread (not a task) keeps CPU-bound parsing and embedding from stalling the API's loop.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._slots = threading.BoundedSemaphore(2)
        self._tasks: set[asyncio.Future[None]] = set()

    def _run(self, work: Callable[[Runtime], Awaitable[None]]) -> None:
        def thread_main() -> None:
            async def go() -> None:
                rt = Runtime.create(self._settings, db_pool_size=4)
                rt.llm = ScriptedLLM()
                try:
                    await work(rt)
                finally:
                    await rt.aclose()

            with self._slots:
                asyncio.run(go())

        task = asyncio.ensure_future(asyncio.to_thread(thread_main))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        async def work(rt: Runtime) -> None:
            async with rt.db.session() as s:
                job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
                project = await s.get(ProjectRow, job.project_id) if job else None
            if project is None or project.upload_object_key is None:
                return  # no network in e2e: a GitHub job (no upload) stays queued
            await run_analysis(rt, rt.llm, job_id)  # type: ignore[arg-type]

        self._run(work)

    async def enqueue_export(self, export_id: str) -> None:
        async def work(rt: Runtime) -> None:
            renderer = PdfRenderer()
            try:
                await run_export(rt, export_id, renderer)
            except ExportError as exc:
                await mark_failed(rt, export_id, exc.message)
            finally:
                await renderer.close()

        self._run(work)

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        self._run(lambda rt: cleanup_project(rt, project_id))

    async def aclose(self) -> None:
        # Threads run to completion (a job is short and the process is a test fixture); nothing to cancel.
        await asyncio.gather(*self._tasks, return_exceptions=True)


def build_app():  # type: ignore[no-untyped-def]
    settings = e2e_settings()
    if LIVE:
        # Worker subprocesses read their settings from the environment: point them at the same isolated stores.
        os.environ.update(
            APP_ENV="test",
            DATABASE_URL=settings.database_url,
            REDIS_URL=settings.redis_url,
            OBJECT_STORE_BUCKET=settings.object_store_bucket,
            QDRANT_COLLECTION_PREFIX=settings.qdrant_collection_prefix,
        )
        return create_app(settings)
    return create_app(settings, queue=InProcessQueue(settings), llm=ScriptedLLM())


if __name__ == "__main__":
    asyncio.run(prepare_database(e2e_settings()))
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
