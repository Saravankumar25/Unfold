"""Redis adapter (ESD §4.5): ephemeral state only — rate limits, caches, LLM coordination. No durable data."""

from __future__ import annotations

from typing import Any

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError

from unfold.core.config import Settings
from unfold.core.errors import StoreUnavailable
from unfold.ports import RateLimiter

# INCR + EXPIRE as one atomic step; also repairs a counter left without a TTL by an older crash (audit S-4).
_HIT = """
local c = redis.call('INCR', KEYS[1])
if c == 1 or redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c
"""


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

    async def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        async for key in self.client.scan_iter(match=f"{prefix}*", count=500):
            deleted += int(await self.client.delete(key))
        return deleted


class RedisRateLimiter(RateLimiter):
    """Fixed-window limiter. Phase 4 swaps in a DynamoDB (TTL items) implementation of the same port."""

    def __init__(self, store: RedisStore) -> None:
        self._store = store
        self._hit = store.client.register_script(_HIT)

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        try:
            count = await self._hit(keys=[f"rl:{key}"], args=[window_seconds])
        except (RedisError, OSError) as exc:  # fail closed, as a typed error the API maps to 503
            raise StoreUnavailable(f"Rate limiter unreachable: {type(exc).__name__}") from exc
        return int(count) <= limit

    async def delete_prefix(self, prefix: str) -> int:
        return await self._store.delete_prefix(f"rl:{prefix}")

    async def ping(self) -> None:
        await self._store.ping()
