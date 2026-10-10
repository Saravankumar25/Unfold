"""One-shot worker entrypoint: `python -m unfold.worker.run <task> <ref>` with task one of

* `analysis <job_id>`    run or resume an analysis job
* `export <export_id>`   render one export artifact (PDF through Chromium, or Markdown)
* `cleanup <project_id>` remove a project from every store
* `sweep`                one pass of the sweeper (expired sessions, deleting projects, stale jobs and exports)

Loads the work, runs it to completion, exits. Exit codes: 0 done (succeeded, or failed for good and recorded on the
job or artifact), 75 returned to the queue and should be run again, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from unfold.cleanup.service import cleanup_project
from unfold.cleanup.sweeper import sweep
from unfold.core.config import get_settings
from unfold.core.errors import ExportError
from unfold.core.logging import configure_logging, get_logger
from unfold.exports.pdf import PdfRenderer
from unfold.exports.service import mark_failed, run_export
from unfold.llm.factory import build_gateway
from unfold.observability.tracing import configure_tracing
from unfold.orchestration.pipeline import RETRY_EXIT_CODE, run_analysis
from unfold.runtime import Runtime
from unfold.worker.queue import SubprocessJobQueue

log = get_logger(__name__)


def sweep_stale_workdirs(max_age_s: float = 3600.0, *, work_dir: str = "") -> int:
    """Remove job workdirs a killed worker left behind (they hold an untrusted extracted repository).

    Sweeps the system temp dir and, when set, `settings.work_dir` (where the pipeline actually creates them).
    """
    roots = [Path(tempfile.gettempdir())]
    if work_dir:
        roots.append(Path(work_dir))
    removed = 0
    for path in (p for root in roots for p in root.glob("unfold-job-*")):
        try:
            if path.is_dir() and time.time() - path.stat().st_mtime > max_age_s:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def _arm_watchdog(seconds: float) -> None:
    """Last resort: in-thread parsing cannot be cancelled, so end the process (exit 75 = run me again)."""
    timer = threading.Timer(seconds, lambda: os._exit(RETRY_EXIT_CODE))
    timer.daemon = True
    timer.start()


async def _export(rt: Runtime, export_id: str) -> int:
    renderer = PdfRenderer()
    try:
        await run_export(rt, export_id, renderer)
        return 0
    except ExportError as exc:
        if exc.retryable:
            log.warning("export.retry", export_id=export_id, error=exc.message)
            return RETRY_EXIT_CODE
        await mark_failed(rt, export_id, exc.message)
        return 0
    finally:
        await renderer.close()


async def _sweep(rt: Runtime) -> int:
    queue = await SubprocessJobQueue.connect(rt.settings)
    try:
        report = await sweep(rt, queue)
        log.info("sweep.report", **report.as_dict())
        await queue.join()  # restarted jobs and exports are supervised by this process; let them finish
    finally:
        await queue.aclose()
    return 0


async def run(task: str, ref: str) -> int:
    settings = get_settings()
    configure_logging(settings)
    configure_tracing(settings, "worker")
    _arm_watchdog(settings.job_timeout_seconds + 30.0)
    swept = sweep_stale_workdirs(work_dir=settings.work_dir)
    if swept:
        log.info("worker.swept_workdirs", count=swept)
    rt = Runtime.create(settings, db_pool_size=4)
    try:
        await rt.objects.ensure_bucket()
        if task == "cleanup":
            await cleanup_project(rt, ref)  # logs its own counts
            return 0
        if task == "export":
            return await _export(rt, ref)
        if task == "sweep":
            return await _sweep(rt)
        gateway = build_gateway(settings, rt.redis)
        rt.llm = gateway
        outcome = await run_analysis(rt, gateway, ref)
        if outcome.succeeded or not outcome.retry:
            return 0
        return RETRY_EXIT_CODE
    finally:
        await rt.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unfold.worker.run")
    parser.add_argument("task", choices=["analysis", "export", "cleanup", "sweep"])
    parser.add_argument("ref", nargs="?", default="", help="job, export or project id (not used by sweep)")
    args = parser.parse_args(argv)
    if args.task != "sweep" and not args.ref:
        parser.error(f"{args.task} needs an id")
    return asyncio.run(run(args.task, args.ref))


if __name__ == "__main__":
    sys.exit(main())
