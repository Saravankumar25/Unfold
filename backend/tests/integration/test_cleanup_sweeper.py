"""Deletion and expiry are durable and complete: every store is emptied, none of it depends on a TTL, and a
crash or restart between the request and the cleanup job loses nothing."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import models as qm
from sqlalchemy import func, select, text, update

from unfold.cleanup.service import cleanup_project, mark_deleting
from unfold.cleanup.sweeper import sweep
from unfold.core.config import Settings
from unfold.core.errors import NotFound
from unfold.evaluation.scripted import ScriptedLLM
from unfold.indexing.dense import DenseIndex
from unfold.indexing.embeddings import embedder_for
from unfold.orchestration.pipeline import run_analysis
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    ArtifactRow,
    Base,
    ChatMessageRow,
    ChatSessionRow,
    ProjectRow,
    SessionRow,
)
from unfold.ports import project_prefix, project_rate_key, session_rate_key
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

API = "/api/v1"
BACKEND = Path(__file__).resolve().parents[2]
Seeded = Any


async def _redis_keys(rt: Runtime, *needles: str) -> list[str]:
    found: list[str] = []
    async for key in rt.redis.client.scan_iter(match="*", count=1000):
        name = key.decode() if isinstance(key, bytes) else str(key)
        if any(n in name for n in needles):
            found.append(name)
    return found


async def inventory(rt: Runtime, seeded: Seeded) -> dict[str, int]:
    """How much of one project is left in every store."""
    pid = seeded.project_id
    out: dict[str, int] = {}
    async with rt.db.session() as s:
        for table in Base.metadata.sorted_tables:
            if "project_id" in table.c:
                where = f"project_id = '{pid}'"
            elif table.name == "projects":
                where = f"id = '{pid}'"
            elif table.name == "job_checkpoints":
                where = f"job_id IN (SELECT id FROM analysis_jobs WHERE project_id = '{pid}')"
            elif table.name == "chat_messages":
                where = f"chat_session_id IN (SELECT id FROM chat_sessions WHERE project_id = '{pid}')"
            else:
                continue
            n = (await s.execute(text(f"SELECT count(*) FROM {table.name} WHERE {where}"))).scalar_one()
            out[f"pg.{table.name}"] = int(n)
    out["blobs"] = sum(
        1
        for _ in rt.objects._client.list_objects(  # type: ignore[attr-defined]
            rt.objects.bucket, prefix=project_prefix(pid), recursive=True
        )
    )
    collection = DenseIndex(rt.qdrant, embedder_for(rt), rt.redis).collection
    if await rt.qdrant.client.collection_exists(collection):
        counted = await rt.qdrant.client.count(
            collection,
            count_filter=qm.Filter(
                must=[qm.FieldCondition(key="project_id", match=qm.MatchValue(value=pid))]
            ),
            exact=True,
        )
        out["vectors"] = int(counted.count)
    else:
        out["vectors"] = 0
    nodes = await rt.neo4j.run("MATCH (n {project_id: $pid}) RETURN count(n) AS n", pid=pid)
    out["graph_nodes"] = int(nodes[0]["n"])
    out["progress"] = len(await rt.progress.read_after(pid, limit=10_000))
    out["redis_project_keys"] = len(await _redis_keys(rt, f"project:{pid}", f"usage:{seeded.job_id}"))
    return {k: v for k, v in out.items() if v or not k.startswith("pg.") or k == "pg.projects"}


def _total(inv: dict[str, int]) -> int:
    return sum(inv.values())


async def _decorate(rt: Runtime, seeded: Seeded) -> uuid.UUID:
    """A chat, an export artifact with a blob, and the rate-limit / usage keys a live project accumulates."""
    chat_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    key = f"{project_prefix(seeded.project_id)}exports/{artifact_id}.md"
    await rt.objects.put_bytes(key, b"# exported", "text/markdown")
    async with rt.db.transaction() as s:
        s.add(ChatSessionRow(id=chat_id, project_id=uuid.UUID(seeded.project_id), title="t"))
        await s.flush()
        s.add(
            ChatMessageRow(chat_session_id=chat_id, seq=1, role="user", mode="default", content={"text": "q"})
        )
        s.add(
            ArtifactRow(
                id=artifact_id,
                project_id=uuid.UUID(seeded.project_id),
                kind="guide_md",
                status="ready",
                object_key=key,
                content_type="text/markdown",
                size_bytes=10,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    await rt.limiter.hit(project_rate_key("export", seeded.project_id), 10, 3600)
    await rt.limiter.hit(session_rate_key("chat", seeded.session_id), 10, 3600)
    await rt.limiter.hit(session_rate_key("projects", seeded.session_id), 10, 3600)
    await rt.redis.client.hset(f"usage:chat:{chat_id}", mapping={"calls": 1})
    await rt.redis.client.hset(f"usage:{seeded.job_id}", mapping={"calls": 1})
    return chat_id


async def test_delete_is_durable_across_an_api_restart(
    open_api: Callable[..., Any],
    analyzed_project: Callable[..., Awaitable[Seeded]],
    runtime: Runtime,
    recording_queue: Any,
) -> None:
    seeded = await analyzed_project()
    chat_id = await _decorate(runtime, seeded)
    before = await inventory(runtime, seeded)
    assert before["vectors"] > 0 and before["graph_nodes"] > 0 and before["blobs"] >= 3
    assert before["progress"] > 0 and before["pg.chunks"] > 0 and before["redis_project_keys"] >= 2

    async with open_api(queue=recording_queue) as client:
        client.cookies.set("unfold_session", seeded.token)
        resp = await client.delete(f"{API}/projects/{seeded.project_id}")
        assert resp.status_code == 202 and resp.json() == {"status": "deleting"}
        assert recording_queue.cleanups == [seeded.project_id]
        # from this moment the project is invisible, whatever has or has not been cleaned yet
        for url in (
            f"{API}/projects/{seeded.project_id}",
            f"{API}/projects/{seeded.project_id}/guide",
            f"{API}/projects/{seeded.project_id}/chats",
            f"{API}/chats/{chat_id}",
            f"{API}/projects/{seeded.project_id}/artifacts",
        ):
            assert (await client.get(url)).status_code == 404, url
        assert (await client.get(f"{API}/sessions/current")).json()["projects"] == []
        assert (await client.delete(f"{API}/projects/{seeded.project_id}")).status_code == 404
    # The queue double never ran the cleanup job: this is the "API restarted before cleanup" case.
    marked = await inventory(runtime, seeded)
    assert marked["vectors"] == before["vectors"] and marked["pg.projects"] == 1
    async with runtime.db.session() as s:
        row = await s.get(ProjectRow, uuid.UUID(seeded.project_id))
    assert row is not None and row.deleting_at is not None

    async with open_api():  # a new API process: its start-up sweep finishes the job
        pass
    after = await inventory(runtime, seeded)
    assert _total(after) == 0, after
    assert await _redis_keys(runtime, f"project:{seeded.project_id}", f"usage:chat:{chat_id}") == []


async def test_expired_session_removes_everything_and_the_sweep_is_idempotent(
    analyzed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime
) -> None:
    seeded = await analyzed_project(session_expires_in=timedelta(minutes=-1))
    await _decorate(runtime, seeded)
    assert _total(await inventory(runtime, seeded)) > 50

    report = await sweep(runtime, None)
    assert report.marked_deleting >= 1 and report.projects_cleaned >= 1 and report.sessions_removed >= 1
    assert _total(await inventory(runtime, seeded)) == 0
    async with runtime.db.session() as s:
        assert await s.get(SessionRow, seeded.session_id) is None
    assert await _redis_keys(runtime, str(seeded.session_id)) == []  # `chat:` and `projects:` keys

    again = await sweep(runtime, None)
    assert (again.marked_deleting, again.projects_cleaned, again.sessions_removed) == (0, 0, 0)


async def test_expired_project_of_a_live_session_and_expired_exports_are_swept(
    analyzed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime, recording_queue: Any
) -> None:
    gone = await analyzed_project(expires_in=timedelta(minutes=-5))
    kept = await analyzed_project()
    try:
        await _decorate(runtime, kept)
        stale_pending = uuid.uuid4()
        expired = uuid.uuid4()
        old = datetime.now(UTC) - timedelta(minutes=30)
        expired_key = f"{project_prefix(kept.project_id)}exports/{expired}.md"
        await runtime.objects.put_bytes(expired_key, b"old", "text/markdown")
        async with runtime.db.transaction() as s:
            for aid, status, key, exp in (
                (stale_pending, "pending", None, datetime.now(UTC) + timedelta(hours=1)),
                (expired, "ready", expired_key, datetime.now(UTC) - timedelta(minutes=1)),
            ):
                s.add(
                    ArtifactRow(
                        id=aid,
                        project_id=uuid.UUID(kept.project_id),
                        kind="guide_md",
                        status=status,
                        object_key=key,
                        content_type="text/markdown",
                        created_at=old,
                        expires_at=exp,
                    )
                )

        report = await sweep(runtime, recording_queue)
        assert _total(await inventory(runtime, gone)) == 0
        assert (await inventory(runtime, kept))["pg.projects"] == 1
        assert report.exports_expired >= 1 and report.exports_requeued >= 1
        assert str(stale_pending) in recording_queue.exports
        with pytest.raises(NotFound):
            await runtime.objects.get_bytes(expired_key)
        async with runtime.db.session() as s:
            assert await s.get(ArtifactRow, expired) is None
    finally:
        await cleanup_project(runtime, kept.project_id)


async def test_cleanup_waits_for_a_live_analysis_worker_then_completes(
    seed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime
) -> None:
    seeded = await seed_project()
    async with runtime.db.transaction() as s:
        await s.execute(
            update(AnalysisJobRow)
            .where(AnalysisJobRow.id == uuid.UUID(seeded.job_id))
            .values(status="running", attempt=1, heartbeat_at=datetime.now(UTC))
        )
    first = await cleanup_project(runtime, seeded.project_id)
    assert first == {"deferred": 1}
    inv = await inventory(runtime, seeded)
    assert inv["pg.projects"] == 1 and inv["blobs"] == 1  # untouched while a worker could still write

    async with runtime.db.transaction() as s:
        await s.execute(
            update(AnalysisJobRow)
            .where(AnalysisJobRow.id == uuid.UUID(seeded.job_id))
            .values(status="failed")
        )
    done = await cleanup_project(runtime, seeded.project_id)
    assert done["projects"] == 1 and done["blobs"] == 1
    assert _total(await inventory(runtime, seeded)) == 0


async def test_a_deleting_project_is_not_analysed(
    seed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime
) -> None:
    seeded = await seed_project()
    try:
        await mark_deleting(runtime, [uuid.UUID(seeded.project_id)])
        outcome = await run_analysis(runtime, ScriptedLLM(), seeded.job_id)
        assert not outcome.succeeded and not outcome.retry
        inv = await inventory(runtime, seeded)
        assert inv.get("pg.chunks", 0) == 0 and inv["vectors"] == 0 and inv["graph_nodes"] == 0
    finally:
        await cleanup_project(runtime, seeded.project_id)


async def test_worker_sweep_task_runs_the_same_function(
    seed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime, settings: Settings
) -> None:
    seeded = await seed_project()
    await mark_deleting(runtime, [uuid.UUID(seeded.project_id)])
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LOG_FORMAT": "console",
        "DATABASE_URL": settings.database_url,
        "REDIS_URL": settings.redis_url,
        "OBJECT_STORE_BUCKET": settings.object_store_bucket,
        "QDRANT_COLLECTION_PREFIX": settings.qdrant_collection_prefix,
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "unfold.worker.run", "sweep", cwd=BACKEND, env=env
    )
    assert await asyncio.wait_for(proc.wait(), 300) == 0
    assert _total(await inventory(runtime, seeded)) == 0
    async with runtime.db.session() as s:
        left = (
            await s.execute(
                select(func.count()).select_from(ProjectRow).where(ProjectRow.deleting_at.is_not(None))
            )
        ).scalar_one()
    assert left == 0


async def test_a_failing_sweep_step_does_not_starve_the_others(
    runtime: Runtime,
    seed_project: Callable[..., Awaitable[Seeded]],
    recording_queue: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_project(session_expires_in=timedelta(minutes=-1))  # an expired session with a project

    async def broken(prefix: str) -> int:
        raise RuntimeError("redis is down")

    monkeypatch.setattr(runtime.limiter, "delete_prefix", broken)
    report = await sweep(runtime, recording_queue)
    monkeypatch.undo()
    assert report.steps_failed >= 1  # the rate-limit key removal failed ...
    assert report.projects_cleaned >= 1  # ... and the cleanup before it, and the steps after it, still ran
    assert _total(await inventory(runtime, seeded)) == 0
    again = await sweep(runtime, None)  # the next pass removes the session that had to wait
    assert again.sessions_removed >= 1
