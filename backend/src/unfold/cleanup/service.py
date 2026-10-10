"""Project cleanup (LLD §19): remove a project from every store. Idempotent; safe to rerun after a crash."""

from __future__ import annotations

import uuid

from sqlalchemy import delete

from unfold.core.logging import get_logger
from unfold.graph.builder import GraphBuilder
from unfold.graph.queries import evict_memory_graph
from unfold.indexing.dense import dense_store_for
from unfold.persistence.postgres.models import ProjectRow
from unfold.ports import project_prefix
from unfold.project_ir.snapshot import evict_project
from unfold.runtime import Runtime

log = get_logger(__name__)


async def cleanup_project(rt: Runtime, project_id: str) -> dict[str, int]:
    """Remove blobs, vectors, graph nodes, progress and every database row of one project.

    Stores are cleared before the project row so a failure leaves the row behind and the call can be repeated.
    """
    project_id = str(uuid.UUID(project_id))  # validate before it becomes a key prefix
    counts: dict[str, int] = {}
    counts["blobs"] = await rt.objects.delete_prefix(project_prefix(project_id))
    evict_project(rt.objects, project_id)
    await dense_store_for(rt).delete_project(project_id)
    evict_memory_graph(rt, project_id)
    counts["graph_nodes"] = (
        await GraphBuilder(rt.neo4j).delete_project(project_id) if rt.settings.graph_backend == "neo4j" else 0
    )
    counts["progress"] = await rt.progress.delete_project(project_id)
    async with rt.db.transaction() as s:
        result = await s.execute(delete(ProjectRow).where(ProjectRow.id == uuid.UUID(project_id)))
        counts["projects"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    return counts
