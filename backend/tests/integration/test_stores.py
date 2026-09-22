from __future__ import annotations

import pytest

from unfold.runtime import Runtime

pytestmark = pytest.mark.integration


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


async def test_event_stream_replay(runtime: Runtime, project_id: str) -> None:
    first = await runtime.redis.publish_event(project_id, {"stage": "INGESTING"})
    await runtime.redis.publish_event(project_id, {"stage": "INVENTORY"})
    all_events = await runtime.redis.read_events(project_id, "0-0", block_ms=100)
    assert [e["stage"] for _, e in all_events] == ["INGESTING", "INVENTORY"]
    after_first = await runtime.redis.read_events(project_id, first, block_ms=100)
    assert [e["stage"] for _, e in after_first] == ["INVENTORY"]
    await runtime.redis.delete_prefix(f"events:{project_id}")


async def test_rate_limit_window(runtime: Runtime, project_id: str) -> None:
    results = [await runtime.redis.hit_rate_limit(f"t:{project_id}", 2, 60) for _ in range(3)]
    assert results == [True, True, False]
