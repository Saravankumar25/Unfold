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
from sqlalchemy import text, update

from unfold.cleanup.service import cleanup_project, mark_deleting
from unfold.cleanup.sweeper import sweep
from unfold.core.config import Settings
from unfold.core.errors import NotFound
from unfold.core.ids import token_hash
from unfold.domain.records import ArtifactRec, ChatMessageRec, ChatRec, JobRec
from unfold.evaluation.scripted import ScriptedLLM
from unfold.indexing.dense_qdrant import DenseIndex
from unfold.indexing.embeddings import embedder_for
from unfold.orchestration.pipeline import run_analysis
from unfold.persistence.postgres.models import AnalysisJobRow, Base
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


async def _artifact(
    rt: Runtime,
    project_id: str,
    status: str,
    key: str | None,
    expires_at: datetime,
    created_at: datetime,
    kind: str = "guide_md",
) -> str:
    rec = ArtifactRec(
        id=str(uuid.uuid4()),
        project_id=project_id,
        kind=kind,
        created_at=created_at,
        expires_at=expires_at,
        status=status,
        object_key=key,
        content_type="text/markdown",
    )
    stored = await rt.control.add_artifact(rec, reuse_since=created_at - timedelta(days=1))
    assert stored.id == rec.id  # a same-kind pending export would have been reused instead
    return rec.id


async def _decorate(rt: Runtime, seeded: Seeded) -> str:
    """A chat, an export artifact with a blob, and the rate-limit / usage keys a live project accumulates."""
    now = datetime.now(UTC)
    chat_id = str(uuid.uuid4())
    await rt.control.create_chat(
        ChatRec(id=chat_id, project_id=seeded.project_id, created_at=now, updated_at=now, title="t"),
        max_per_project=20,
    )
    msg = {"chat_id": chat_id, "created_at": now, "mode": "default"}
    await rt.control.append_turn(
        chat_id,
        ChatMessageRec(id=str(uuid.uuid4()), role="user", content={"text": "q"}, **msg),
        ChatMessageRec(id=str(uuid.uuid4()), role="assistant", content={"text": "a"}, **msg),
        title_chars=60,
    )
    artifact_id = str(uuid.uuid4())
    key = f"{project_prefix(seeded.project_id)}exports/{artifact_id}.md"
    await rt.objects.put_bytes(key, b"# exported", "text/markdown")
    await _artifact(rt, seeded.project_id, "ready", key, now + timedelta(hours=1), now)
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
    row = await runtime.control.get_project(seeded.project_id)
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
    assert await runtime.control.session_by_token(token_hash(seeded.token)) is None
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
        old = datetime.now(UTC) - timedelta(minutes=30)
        expired_key = f"{project_prefix(kept.project_id)}exports/old.md"
        await runtime.objects.put_bytes(expired_key, b"old", "text/markdown")
        stale_pending = await _artifact(
            runtime,
            kept.project_id,
            "pending",
            None,
            datetime.now(UTC) + timedelta(hours=1),
            old,
            "guide_pdf",
        )
        expired = await _artifact(
            runtime, kept.project_id, "ready", expired_key, datetime.now(UTC) - timedelta(minutes=1), old
        )

        report = await sweep(runtime, recording_queue)
        assert _total(await inventory(runtime, gone)) == 0
        assert (await inventory(runtime, kept))["pg.projects"] == 1
        assert report.exports_expired >= 1 and report.exports_requeued >= 1
        assert stale_pending in recording_queue.exports
        with pytest.raises(NotFound):
            await runtime.objects.get_bytes(expired_key)
        assert await runtime.control.get_artifact(expired) is None
    finally:
        await cleanup_project(runtime, kept.project_id)


async def test_cleanup_waits_for_a_live_analysis_worker_then_completes(
    seed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime
) -> None:
    seeded = await seed_project()

    def running(job: JobRec) -> bool:
        job.status, job.attempt, job.heartbeat_at = "running", 1, datetime.now(UTC)
        return True

    await runtime.control.update_job(seeded.job_id, running)
    first = await cleanup_project(runtime, seeded.project_id)
    assert first == {"deferred": 1}
    inv = await inventory(runtime, seeded)
    assert inv["pg.projects"] == 1 and inv["blobs"] == 1  # untouched while a worker could still write

    def failed(job: JobRec) -> bool:
        job.status = "failed"
        return True

    await runtime.control.update_job(seeded.job_id, failed)
    done = await cleanup_project(runtime, seeded.project_id)
    assert done["projects"] == 1 and done["blobs"] == 1
    assert _total(await inventory(runtime, seeded)) == 0


async def test_a_deleting_project_is_not_analysed(
    seed_project: Callable[..., Awaitable[Seeded]], runtime: Runtime
) -> None:
    seeded = await seed_project()
    try:
        await mark_deleting(runtime, [seeded.project_id])
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
    await mark_deleting(runtime, [seeded.project_id])
    # The sweep restarts every unfinished job it finds. Nothing but this project may be left to run, and the
    # worker must never reach a real model: no provider keys in its environment.
    async with runtime.db.transaction() as s:
        await s.execute(
            update(AnalysisJobRow)
            .where(AnalysisJobRow.status.in_(["queued", "running"]))
            .values(status="failed", heartbeat_at=None)
        )
    no_keys = {f"{p}_API_KEY_{i}": "" for p in ("GEMINI", "GROQ") for i in range(1, 5)}
    env = {
        **os.environ,
        **no_keys,
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
    assert await runtime.control.deleting_projects(10) == []


async def test_a_failing_sweep_step_does_not_starve_the_others(
    runtime: Runtime,
    seed_project: Callable[..., Awaitable[Seeded]],
    recording_queue: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_project(session_expires_in=timedelta(minutes=-1))  # expired session, one project
    real = runtime.limiter.delete_prefix

    async def flaky(prefix: str) -> int:
        if prefix.startswith(
            ("chat:", "projects:")
        ):  # the session-key step; project cleanup uses export:project:
            raise RuntimeError("redis is down")
        return await real(prefix)

    monkeypatch.setattr(runtime.limiter, "delete_prefix", flaky)
    report = await sweep(runtime, recording_queue)
    monkeypatch.undo()
    assert report.steps_failed >= 1  # removing the session's rate-limit keys failed ...
    assert report.projects_cleaned >= 1  # ... but the cleanup before it still ran
    assert _total(await inventory(runtime, seeded)) == 0
    again = await sweep(runtime, None)  # the session that had to wait goes on the next pass
    assert again.sessions_removed >= 1
