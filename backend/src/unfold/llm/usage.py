"""Per-job usage accounting and admission in Redis (the local-stack `UsageStore`).

The admission script is atomic: it counts the call and applies the hard call cap, the token cap and the soft
cap for low-priority calls in one step. `client` is a redis-py asyncio client handed in by the composition root.
"""

from __future__ import annotations

from typing import Any

from unfold.llm._redis import resolve

USAGE_TTL_SECONDS = 7 * 86_400

# KEYS: usage hash   ARGV: soft_budget, hard_budget, priority, ttl, token_budget
#   → {1, calls} | {0, 'soft'|'hard'|'tokens'}
_ADMIT = """
local calls = tonumber(redis.call('HGET', KEYS[1], 'calls') or '0')
if calls >= tonumber(ARGV[2]) then return {0, 'hard'} end
local used = tonumber(redis.call('HGET', KEYS[1], 'input_tokens') or '0')
  + tonumber(redis.call('HGET', KEYS[1], 'output_tokens') or '0')
if used >= tonumber(ARGV[5]) then return {0, 'tokens'} end
if tonumber(ARGV[3]) >= 2 and calls >= tonumber(ARGV[1]) then return {0, 'soft'} end
local n = redis.call('HINCRBY', KEYS[1], 'calls', 1)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return {1, n}
"""


class RedisUsageStore:
    def __init__(self, client: Any, *, prefix: str = "llm") -> None:
        self._client = client
        self._prefix = prefix
        self._admit = client.register_script(_ADMIT)

    def _key(self, job_id: str) -> str:
        return f"usage:{job_id}" if self._prefix == "llm" else f"{self._prefix}:usage:{job_id}"

    async def admit(self, job_id: str, *, soft: int, hard: int, priority: int, tokens: int) -> str | None:
        result: Any = await self._admit(
            keys=[self._key(job_id)], args=[soft, hard, priority, USAGE_TTL_SECONDS, tokens]
        )
        if int(result[0]) == 1:
            return None
        return result[1].decode() if isinstance(result[1], bytes) else str(result[1])

    async def reject(self, job_id: str) -> None:
        await resolve(self._client.hincrby(self._key(job_id), "budget_rejections", 1))

    async def add(self, job_id: str, increments: dict[str, int]) -> None:
        pipe = self._client.pipeline(transaction=False)
        for name, amount in increments.items():
            if amount:
                pipe.hincrby(self._key(job_id), name, int(amount))
        pipe.expire(self._key(job_id), USAGE_TTL_SECONDS)
        await pipe.execute()

    async def read(self, job_id: str) -> dict[str, int]:
        raw = await resolve(self._client.hgetall(self._key(job_id)))
        return {(k.decode() if isinstance(k, bytes) else str(k)): int(v) for k, v in raw.items()}
