from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from unfold.persistence.postgres.models import ProjectRow, SessionRow
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration


@pytest.fixture
async def new_project(runtime: Runtime) -> AsyncIterator[str]:
    """A real project row (progress events reference it); deleting the session cascades everything."""
    now = datetime.now(UTC)
    session = SessionRow(
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        created_at=now,
        last_active_at=now,
        expires_at=now + timedelta(hours=1),
        status="active",
    )
    project_id = uuid.uuid4()
    async with runtime.db.transaction() as s:
        s.add(session)
        await s.flush()
        s.add(
            ProjectRow(
                id=project_id,
                session_id=session.id,
                input_type="github",
                technical_level="beginner",
                analysis_version="test",
                expires_at=now + timedelta(hours=1),
            )
        )
    yield str(project_id)
    async with runtime.db.transaction() as s:
        row = await s.get(SessionRow, session.id)
        if row is not None:
            await s.delete(row)


async def test_all_stores_reachable(runtime: Runtime) -> None:
    await runtime.db.ping()
    await runtime.redis.ping()
    await runtime.neo4j.ping()
    await runtime.qdrant.ping()
    await runtime.objects.ping()


async def test_object_store_roundtrip_and_prefix_delete(runtime: Runtime, project_id: str) -> None:
    key = f"projects/{project_id}/ir/1.json.gz"
    await runtime.objects.put_bytes(key, b"hello")
    assert await runtime.objects.get_bytes(key) == b"hello"
    assert await runtime.objects.delete_prefix(f"projects/{project_id}/") == 1


async def test_progress_store_is_ordered_and_replayable(runtime: Runtime, new_project: str) -> None:
    first = await runtime.progress.publish(new_project, {"stage": "INGESTING"})
    second = await runtime.progress.publish(new_project, {"stage": "INVENTORY"})
    assert second > first
    everything = await runtime.progress.read_after(new_project)
    assert [e["stage"] for _, e in everything] == ["INGESTING", "INVENTORY"]
    after_first = await runtime.progress.read_after(new_project, first)
    assert [e["stage"] for _, e in after_first] == ["INVENTORY"]
    assert await runtime.progress.read_after(new_project, second) == []
    assert await runtime.progress.delete_project(new_project) == 2


async def test_rate_limit_window(runtime: Runtime, project_id: str) -> None:
    results = [await runtime.limiter.hit(f"t:{project_id}", 2, 60) for _ in range(3)]
    assert results == [True, True, False]
    await runtime.limiter.delete_prefix(f"t:{project_id}")


async def test_rate_limit_counter_without_ttl_is_repaired(runtime: Runtime, project_id: str) -> None:
    """A key left without a TTL (crash between INCR and EXPIRE in the old implementation) must still expire."""
    key = f"rl:t:{project_id}"
    await runtime.redis.client.set(key, 5)
    assert await runtime.redis.client.ttl(key) == -1
    assert await runtime.limiter.hit(f"t:{project_id}", 10, 60)
    assert 0 < await runtime.redis.client.ttl(key) <= 60
    await runtime.limiter.delete_prefix(f"t:{project_id}")
