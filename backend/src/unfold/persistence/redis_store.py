"""Redis adapter (ESD §4.5): ephemeral state only — queues, progress streams, rate limits, caches, locks."""

from __future__ import annotations

from typing import Any

import orjson
from redis.asyncio import Redis

from unfold.core.config import Settings


def events_key(project_id: str) -> str:
    return f"events:{project_id}"


class RedisStore:
    def __init__(self, settings: Settings) -> None:
        self.client: Redis = Redis.from_url(settings.redis_url, decode_responses=False)

    async def ping(self) -> None:
        await self.client.ping()

    async def aclose(self) -> None:
        await self.client.aclose()

    # --- JSON cache -------------------------------------------------------------------------------
    async def get_json(self, key: str) -> Any | None:
        raw = await self.client.get(key)
        return None if raw is None else orjson.loads(raw)

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        await self.client.set(key, orjson.dumps(value), ex=ttl_seconds)

    # --- progress event streams (replayable SSE, HLD D10) ----------------------------------------
    async def publish_event(self, project_id: str, event: dict[str, Any], *, maxlen: int = 500) -> str:
        entry_id = await self.client.xadd(
            events_key(project_id), {"data": orjson.dumps(event)}, maxlen=maxlen, approximate=True
        )
        await self.client.expire(events_key(project_id), 7 * 86_400)
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    async def read_events(
        self, project_id: str, after: str = "0-0", *, block_ms: int = 15_000, count: int = 100
    ) -> list[tuple[str, dict[str, Any]]]:
        resp = await self.client.xread({events_key(project_id): after}, block=block_ms, count=count)
        out: list[tuple[str, dict[str, Any]]] = []
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
                out.append((eid, orjson.loads(fields[b"data"])))
        return out

    # --- fixed-window rate limit ------------------------------------------------------------------
    async def hit_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True when the call is allowed (counter within limit for the current window)."""
        full = f"rl:{key}"
        count = await self.client.incr(full)
        if count == 1:
            await self.client.expire(full, window_seconds)
        return int(count) <= limit

    async def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        async for key in self.client.scan_iter(match=f"{prefix}*", count=500):
            deleted += int(await self.client.delete(key))
        return deleted
