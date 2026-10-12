"""Project cleanup (LLD §21): remove a project from every store. Idempotent; safe to rerun after a crash.

Correctness never depends on TTLs: the `deleting_at` marker on the project record is durable, hides the project
from every route, and keeps the record (and so the sweeper's ability to find it) until every other store is clean.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from unfold.core.errors import NotFound
from unfold.core.logging import get_logger
from unfold.domain.records import ProjectRec
from unfold.graph.builder import GraphBuilder
from unfold.graph.queries import evict_memory_graph
from unfold.indexing.dense import dense_store_for
from unfold.llm.counter_coordination import usage_key
from unfold.llm.schemas import CHAT_JOB_PREFIX
from unfold.orchestration.constants import ALIVE_WITHIN
from unfold.ports import PROJECT_RATE_BUCKETS, project_prefix, project_rate_key
from unfold.project_ir.snapshot import evict_project
from unfold.runtime import Runtime

log = get_logger(__name__)


async def mark_deleting(rt: Runtime, project_ids: Sequence[str]) -> int:
    """Durably mark projects as being deleted (first mark wins); returns how many were newly marked."""
    marked = 0
    for project_id in project_ids:
        newly = False

        def mark(row: ProjectRec) -> bool:
            nonlocal newly
            newly = False  # the store may run this again after a lost race
            if row.deleting_at is not None:
                return False
            row.deleting_at = datetime.now(UTC)
            newly = True
            return True

        try:
            await rt.control.update_project(project_id, mark)
        except NotFound:
            continue  # already removed
        marked += int(newly)
    return marked


async def _worker_alive(rt: Runtime, project_id: str) -> bool:
    """A running analysis with a fresh heartbeat would write behind the cleanup; wait for it to stop."""
    cutoff = datetime.now(UTC) - ALIVE_WITHIN
    return any(
        j.status == "running" and j.heartbeat_at is not None and j.heartbeat_at > cutoff
        for j in await rt.control.jobs_of_project(project_id)
    )


async def _delete_usage(rt: Runtime, ids: list[str]) -> int:
    """Per-job and per-chat LLM usage counters (kept 7 days otherwise)."""
    if not ids:
        return 0
    if rt.counters is not None:
        return await rt.counters.delete([usage_key(i) for i in ids])
    return int(await rt.redis.client.delete(*[f"usage:{i}" for i in ids]))


async def cleanup_project(rt: Runtime, project_id: str) -> dict[str, int]:
    """Remove blobs, vectors, graph nodes, progress, rate-limit and usage keys and every control record.

    Stores are cleared before the project record so a failure leaves it behind and the call can be repeated.
    Returns counts per store; `{"deferred": 1}` when a live analysis worker still owns the project (the marker
    stays and the next sweep finishes the job once the worker has stopped at its next stage boundary).
    """
    key = str(uuid.UUID(project_id))  # validate before it becomes a key prefix
    await mark_deleting(rt, [key])
    if await _worker_alive(rt, key):
        log.info("cleanup.deferred", project_id=key)
        return {"deferred": 1}
    job_ids = [j.id for j in await rt.control.jobs_of_project(key)]
    chat_ids = [c.id for c in await rt.control.chats_of_project(key)]
    counts: dict[str, int] = {}
    counts["blobs"] = await rt.objects.delete_prefix(project_prefix(key))
    evict_project(rt.objects, key)
    await dense_store_for(rt).delete_project(key)
    evict_memory_graph(rt, key)
    counts["graph_nodes"] = (
        await GraphBuilder(rt.neo4j).delete_project(key) if rt.settings.graph_backend == "neo4j" else 0
    )
    counts["progress"] = await rt.progress.delete_project(key)
    limiter_keys = 0
    for bucket in PROJECT_RATE_BUCKETS:
        limiter_keys += await rt.limiter.delete_prefix(project_rate_key(bucket, key))
    counts["rate_limit_keys"] = limiter_keys
    counts["usage_keys"] = await _delete_usage(rt, [*job_ids, *(f"{CHAT_JOB_PREFIX}{i}" for i in chat_ids)])
    counts["projects"] = await rt.control.delete_project_data(key)
    log.info("cleanup.done", project_id=key, **counts)
    return counts
