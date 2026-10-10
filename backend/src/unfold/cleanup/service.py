"""Project cleanup (LLD §21): remove a project from every store. Idempotent; safe to rerun after a crash.

Correctness never depends on TTLs: the `deleting_at` marker on the project row is durable, hides the project from
every route, and keeps the row (and so the sweeper's ability to find it) until every other store is clean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update

from unfold.core.logging import get_logger
from unfold.graph.builder import GraphBuilder
from unfold.indexing.dense import DenseIndex
from unfold.indexing.embeddings import embedder_for
from unfold.llm.schemas import CHAT_JOB_PREFIX
from unfold.orchestration.pipeline import ALIVE_WITHIN
from unfold.persistence.postgres.models import AnalysisJobRow, ChatSessionRow, ProjectRow
from unfold.ports import PROJECT_RATE_BUCKETS, project_prefix, project_rate_key
from unfold.project_ir.snapshot import evict_project
from unfold.runtime import Runtime

log = get_logger(__name__)


async def mark_deleting(rt: Runtime, project_ids: list[uuid.UUID]) -> int:
    """Durably mark projects as being deleted (first mark wins); returns how many rows were newly marked."""
    if not project_ids:
        return 0
    async with rt.db.transaction() as s:
        result = await s.execute(
            update(ProjectRow)
            .where(ProjectRow.id.in_(project_ids), ProjectRow.deleting_at.is_(None))
            .values(deleting_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _worker_alive(rt: Runtime, project_id: uuid.UUID) -> bool:
    """A running analysis with a fresh heartbeat would write behind the cleanup; wait for it to stop."""
    cutoff = datetime.now(UTC) - ALIVE_WITHIN
    async with rt.db.session() as s:
        found = (
            await s.execute(
                select(AnalysisJobRow.id)
                .where(
                    AnalysisJobRow.project_id == project_id,
                    AnalysisJobRow.status == "running",
                    AnalysisJobRow.heartbeat_at > cutoff,
                )
                .limit(1)
            )
        ).first()
    return found is not None


async def cleanup_project(rt: Runtime, project_id: str) -> dict[str, int]:
    """Remove blobs, vectors, graph nodes, progress, rate-limit and usage keys and every database row.

    Stores are cleared before the project row so a failure leaves the row behind and the call can be repeated.
    Returns counts per store; `{"deferred": 1}` when a live analysis worker still owns the project (the marker
    stays and the next sweep finishes the job once the worker has stopped at its next stage boundary).
    """
    pid = uuid.UUID(project_id)  # validate before it becomes a key prefix
    key = str(pid)
    await mark_deleting(rt, [pid])
    if await _worker_alive(rt, pid):
        log.info("cleanup.deferred", project_id=key)
        return {"deferred": 1}
    async with rt.db.session() as s:
        job_ids = [
            str(i)
            for i in (
                await s.execute(select(AnalysisJobRow.id).where(AnalysisJobRow.project_id == pid))
            ).scalars()
        ]
        chat_ids = [
            str(i)
            for i in (
                await s.execute(select(ChatSessionRow.id).where(ChatSessionRow.project_id == pid))
            ).scalars()
        ]
    counts: dict[str, int] = {}
    counts["blobs"] = await rt.objects.delete_prefix(project_prefix(key))
    evict_project(rt.objects, key)
    await DenseIndex(rt.qdrant, embedder_for(rt), rt.redis).delete_project(key)
    counts["graph_nodes"] = await GraphBuilder(rt.neo4j).delete_project(key)
    counts["progress"] = await rt.progress.delete_project(key)
    limiter_keys = 0
    for bucket in PROJECT_RATE_BUCKETS:
        limiter_keys += await rt.limiter.delete_prefix(project_rate_key(bucket, key))
    counts["rate_limit_keys"] = limiter_keys
    usage = [f"usage:{i}" for i in job_ids] + [f"usage:{CHAT_JOB_PREFIX}{i}" for i in chat_ids]
    counts["usage_keys"] = int(await rt.redis.client.delete(*usage)) if usage else 0
    async with rt.db.transaction() as s:
        result = await s.execute(delete(ProjectRow).where(ProjectRow.id == pid))
        counts["projects"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    log.info("cleanup.done", project_id=key, **counts)
    return counts
