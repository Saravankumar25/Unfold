"""One-shot worker entrypoint: `python -m unfold.worker.run analysis <job_id>` / `cleanup <project_id>`.

Loads the job, runs it to completion, exits. Exit codes: 0 done (succeeded, or failed for good and recorded on the
job), 75 the job was returned to the queue and should be run again, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from unfold.cleanup.service import cleanup_project
from unfold.core.config import get_settings
from unfold.core.logging import configure_logging, get_logger
from unfold.llm.factory import build_gateway
from unfold.observability.tracing import configure_tracing
from unfold.orchestration.pipeline import RETRY_EXIT_CODE, run_analysis
from unfold.runtime import Runtime

log = get_logger(__name__)


async def run(task: str, ref: str) -> int:
    settings = get_settings()
    configure_logging(settings)
    configure_tracing(settings, "worker")
    rt = Runtime.create(settings, db_pool_size=4)
    try:
        await rt.objects.ensure_bucket()
        if task == "cleanup":
            counts = await cleanup_project(rt, ref)
            log.info("cleanup.done", project_id=ref, **counts)
            return 0
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
    parser.add_argument("task", choices=["analysis", "cleanup"])
    parser.add_argument("ref", help="analysis job id, or project id for cleanup")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.task, args.ref))


if __name__ == "__main__":
    sys.exit(main())
