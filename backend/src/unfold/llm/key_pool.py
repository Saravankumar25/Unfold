"""Credential pool per provider (LLD §13, ESD §18). Keys are credentials, not quota multipliers.

Health lives in Redis hash `{prefix}:key:{provider}:{idx}` with `last_success`, `last_429`, `last_used`,
`consecutive_failures`, `cooldown_until`, `disabled`, `disabled_until` (all ms since epoch). Selection is least
recently used among healthy keys (not disabled, not cooling). Only key *indices* are ever stored or logged.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from unfold.llm._redis import resolve

_DAY_MS = 86_400_000

# KEYS: one hash per key index (ordered)  → selected index (0-based) or -1
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local best = -1
local best_used = nil
for i, key in ipairs(KEYS) do
  local h = redis.call('HMGET', key, 'disabled', 'disabled_until', 'cooldown_until', 'last_used')
  local disabled = (h[1] == '1') and (tonumber(h[2] or '0') > now)
  local cooling = tonumber(h[3] or '0') > now
  if not disabled and not cooling then
    local used = tonumber(h[4] or '0')
    if best_used == nil or used < best_used then
      best = i - 1
      best_used = used
    end
  end
end
if best >= 0 then
  redis.call('HSET', KEYS[best + 1], 'last_used', now)
  redis.call('PEXPIRE', KEYS[best + 1], 604800000)
end
return best
"""

_NOW = """
local t = redis.call('TIME')
return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
"""


class KeyPool:
    def __init__(self, redis: Redis, *, prefix: str = "llm", disable_seconds: float = 3600.0) -> None:
        self._redis = redis
        self._prefix = prefix
        self._disable_ms = int(disable_seconds * 1000)
        self._acquire = redis.register_script(_ACQUIRE)
        self._now = redis.register_script(_NOW)

    def key(self, provider: str, index: int) -> str:
        return f"{self._prefix}:key:{provider}:{index}"

    async def _now_ms(self) -> int:
        return int(await self._now())

    async def acquire(self, provider: str, key_count: int) -> int | None:
        """Least-recently-used healthy key index, or None when every key is disabled or cooling down."""
        if key_count <= 0:
            return None
        idx = int(await self._acquire(keys=[self.key(provider, i) for i in range(key_count)]))
        return None if idx < 0 else idx

    async def record_success(self, provider: str, index: int) -> None:
        now = await self._now_ms()
        await resolve(
            self._redis.hset(
                self.key(provider, index), mapping={"last_success": now, "consecutive_failures": 0}
            )
        )

    async def record_failure(self, provider: str, index: int) -> None:
        await resolve(self._redis.hincrby(self.key(provider, index), "consecutive_failures", 1))

    async def record_rate_limited(self, provider: str, index: int, cooldown_s: float | None = None) -> None:
        """Mark a 429. `cooldown_s` is applied to this key only when quotas are per key."""
        now = await self._now_ms()
        mapping: dict[str, Any] = {"last_429": now}
        if cooldown_s is not None and cooldown_s > 0:
            mapping["cooldown_until"] = now + int(cooldown_s * 1000)
        await resolve(self._redis.hset(self.key(provider, index), mapping=mapping))

    async def disable(self, provider: str, index: int) -> None:
        """Credential rejected: take it out of rotation (auto re-enabled after `disable_seconds`)."""
        now = await self._now_ms()
        key = self.key(provider, index)
        await resolve(
            self._redis.hset(key, mapping={"disabled": 1, "disabled_until": now + self._disable_ms})
        )
        await resolve(self._redis.hincrby(key, "consecutive_failures", 1))
        await resolve(self._redis.pexpire(key, max(self._disable_ms, _DAY_MS)))

    async def enable(self, provider: str, index: int) -> None:
        await resolve(
            self._redis.hset(self.key(provider, index), mapping={"disabled": 0, "disabled_until": 0})
        )

    async def health(self, provider: str, key_count: int) -> list[dict[str, Any]]:
        """Per-key health (index only, never key material)."""
        now = await self._now_ms()
        out: list[dict[str, Any]] = []
        for i in range(key_count):
            raw = await resolve(self._redis.hgetall(self.key(provider, i)))
            h = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in raw.items()
            }
            disabled = h.get("disabled") == "1" and int(h.get("disabled_until", 0) or 0) > now
            cooling = int(h.get("cooldown_until", 0) or 0) > now
            out.append(
                {
                    "index": i,
                    "healthy": not disabled and not cooling,
                    "disabled": disabled,
                    "cooldown_until": int(h.get("cooldown_until", 0) or 0) or None,
                    "consecutive_failures": int(h.get("consecutive_failures", 0) or 0),
                    "last_success": int(h.get("last_success", 0) or 0) or None,
                    "last_429": int(h.get("last_429", 0) or 0) or None,
                }
            )
        return out
