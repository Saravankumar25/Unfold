"""Provider capacity budget in Redis (LLD §13, AI_infrastructure §8). All checks are atomic Lua scripts.

Scope is per model (`LLM_QUOTA_SCOPE=shared`, the default: keys are credentials, not quota multipliers) or per
(model, key) when the operator asserts separate projects (`per_key`). Per scope:

* `…:rpm`  ZSET  member=reservation id, score=ms                   — 60 s sliding window request count
* `…:tpm`  ZSET  member="<rid>|<tokens>", score=ms                  — 60 s sliding window token sum
* `…:rpd:<utc-day>` STRING counter (expires after 2 days)           — requests per UTC day
* `…:conc` ZSET  member=reservation id, score=lease expiry ms       — concurrency slots; crashed holders expire
* `…:cooldown` STRING until-ms (PX)                                  — set on 429 from `retryDelay`
"""

from __future__ import annotations

import uuid
from typing import Any

from redis.asyncio import Redis

from unfold.core.config import Settings
from unfold.llm.coordination import CONCURRENCY_WAIT_S, WAIT_BUDGET_S, Reservation, ReservingBudget

__all__ = ["CONCURRENCY_WAIT_S", "WAIT_BUDGET_S", "ProviderBudget", "Reservation"]

WINDOW_MS = 60_000

# KEYS: rpm, tpm, rpd_prefix, conc, cooldown
# ARGV: rid, est_tokens, rpm_limit, tpm_limit, rpd_limit, conc_limit, lease_ms
# → {1, now_ms, day} | {0, wait_hint_ms, reason}
_RESERVE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local day = math.floor(tonumber(t[1]) / 86400)
local rid = ARGV[1]
local est = tonumber(ARGV[2])
local rpm_l = tonumber(ARGV[3])
local tpm_l = tonumber(ARGV[4])
local rpd_l = tonumber(ARGV[5])
local conc_l = tonumber(ARGV[6])
local lease = tonumber(ARGV[7])

local cd = tonumber(redis.call('GET', KEYS[5]) or '0')
if cd > now then return {0, cd - now, 'cooldown'} end

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 60000)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 60000)
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now)

local rpd_key = KEYS[3] .. day
local used_day = tonumber(redis.call('GET', rpd_key) or '0')
if used_day >= rpd_l then
  return {0, (day + 1) * 86400000 - now, 'rpd'}
end

if redis.call('ZCARD', KEYS[1]) >= rpm_l then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, math.max(1, tonumber(oldest[2]) + 60000 - now), 'rpm'}
end

local members = redis.call('ZRANGE', KEYS[2], 0, -1)
local used_tok = 0
for _, m in ipairs(members) do
  local sep = string.find(m, '|', 1, true)
  if sep then used_tok = used_tok + tonumber(string.sub(m, sep + 1)) end
end
if used_tok > 0 and used_tok + est > tpm_l then
  local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  return {0, math.max(1, tonumber(oldest[2]) + 60000 - now), 'tpm'}
end

if redis.call('ZCARD', KEYS[4]) >= conc_l then
  local first = redis.call('ZRANGE', KEYS[4], 0, 0, 'WITHSCORES')
  return {0, math.max(1, tonumber(first[2]) - now), 'concurrency'}
end

redis.call('ZADD', KEYS[1], now, rid)
redis.call('ZADD', KEYS[2], now, rid .. '|' .. est)
redis.call('ZADD', KEYS[4], now + lease, rid)
redis.call('INCR', rpd_key)
redis.call('PEXPIRE', rpd_key, 172800000)
redis.call('PEXPIRE', KEYS[1], 120000)
redis.call('PEXPIRE', KEYS[2], 120000)
redis.call('PEXPIRE', KEYS[4], lease + 60000)
return {1, now, day}
"""

# KEYS: tpm, conc   ARGV: rid, old_member, score, actual_tokens (-1 = keep estimate)
_COMMIT = """
redis.call('ZREM', KEYS[2], ARGV[1])
local actual = tonumber(ARGV[4])
if actual >= 0 then
  if redis.call('ZREM', KEYS[1], ARGV[2]) == 1 then
    redis.call('ZADD', KEYS[1], tonumber(ARGV[3]), ARGV[1] .. '|' .. actual)
  end
end
return 1
"""

# KEYS: rpm, tpm, rpd_key, conc   ARGV: rid, tpm_member — undo a reservation that never reached the provider
_RELEASE = """
redis.call('ZREM', KEYS[4], ARGV[1])
local removed = redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[2])
if removed == 1 then
  local v = tonumber(redis.call('GET', KEYS[3]) or '0')
  if v > 0 then redis.call('DECR', KEYS[3]) end
end
return 1
"""

# KEYS: cooldown   ARGV: seconds_ms — extend only (never shorten an existing cooldown)
_COOLDOWN = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local want = now + tonumber(ARGV[1])
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if want > cur then
  redis.call('SET', KEYS[1], want, 'PX', tonumber(ARGV[1]))
end
return want
"""


class ProviderBudget(ReservingBudget):
    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        *,
        prefix: str = "llm",
        lease_seconds: float | None = None,
        step_seconds: float = 1.0,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._prefix = prefix
        self._lease_ms = int((lease_seconds or settings.llm_request_timeout_seconds + 30.0) * 1000)
        self._step = min(1.0, step_seconds)
        self._reserve = redis.register_script(_RESERVE)
        self._commit = redis.register_script(_COMMIT)
        self._release = redis.register_script(_RELEASE)
        self._cooldown = redis.register_script(_COOLDOWN)

    def scope(self, model: str, key_index: int | None = None) -> str:
        if self._settings.llm_quota_scope == "per_key" and key_index is not None:
            return f"{model}:k{key_index}"
        return model

    def _base(self, scope: str) -> str:
        return f"{self._prefix}:budget:{scope}"

    def _keys(self, scope: str) -> list[str]:
        base = self._base(scope)
        return [f"{base}:rpm", f"{base}:tpm", f"{base}:rpd:", f"{base}:conc", f"{base}:cooldown"]

    async def try_reserve(
        self, model: str, est_tokens: int, *, key_index: int | None = None
    ) -> tuple[Reservation | None, float, str]:
        """One atomic attempt. Returns (reservation, wait_hint_seconds, reason)."""
        scope = self.scope(model, key_index)
        limits = self._settings.model_limits(model)
        rid = uuid.uuid4().hex
        result: Any = await self._reserve(
            keys=self._keys(scope),
            args=[
                rid,
                max(0, int(est_tokens)),
                int(limits.get("rpm", 5)),
                int(limits.get("tpm", 200_000)),
                int(limits.get("rpd", 100)),
                int(limits.get("concurrency", 2)),
                self._lease_ms,
            ],
        )
        ok, value, extra = int(result[0]), int(result[1]), result[2]
        if ok == 1:
            res = Reservation(rid, model, scope, max(0, int(est_tokens)), value, int(extra), key_index)
            return res, 0.0, "ok"
        reason = extra.decode() if isinstance(extra, bytes) else str(extra)
        return None, value / 1000.0, reason

    async def commit(self, reservation: Reservation, actual_tokens: int | None = None) -> None:
        """The call reached the provider: keep the request counted, replace the token estimate, free the slot."""
        keys = self._keys(reservation.scope)
        await self._commit(
            keys=[keys[1], keys[3]],
            args=[
                reservation.id,
                f"{reservation.id}|{reservation.est_tokens}",
                reservation.ts_ms,
                -1 if actual_tokens is None else max(0, int(actual_tokens)),
            ],
        )

    async def release(self, reservation: Reservation) -> None:
        """The call never reached the provider: undo the reservation entirely."""
        keys = self._keys(reservation.scope)
        await self._release(
            keys=[keys[0], keys[1], f"{keys[2]}{reservation.day}", keys[3]],
            args=[reservation.id, f"{reservation.id}|{reservation.est_tokens}"],
        )

    async def cooldown(self, model: str, seconds: float, *, key_index: int | None = None) -> None:
        scope = self.scope(model, key_index)
        await self._cooldown(keys=[self._keys(scope)[4]], args=[max(1, int(seconds * 1000))])

    async def cooldown_remaining(self, model: str, *, key_index: int | None = None) -> float:
        raw = await self._redis.get(self._keys(self.scope(model, key_index))[4])
        if raw is None:
            return 0.0
        now_s, now_us = await self._redis.time()
        now_ms = int(now_s) * 1000 + int(now_us) // 1000
        return max(0.0, (int(raw) - now_ms) / 1000.0)

    async def snapshot(self, model: str, *, key_index: int | None = None) -> dict[str, int]:
        """Current usage for diagnostics/tests (does not prune expired entries)."""
        keys = self._keys(self.scope(model, key_index))
        now_s, _ = await self._redis.time()
        day = int(now_s) // 86_400
        members = await self._redis.zrange(keys[1], 0, -1)
        tokens = 0
        for m in members:
            text = m.decode() if isinstance(m, bytes) else str(m)
            tokens += int(text.rsplit("|", 1)[1]) if "|" in text else 0
        return {
            "rpm": int(await self._redis.zcard(keys[0])),
            "tpm": tokens,
            "rpd": int(await self._redis.get(f"{keys[2]}{day}") or 0),
            "concurrency": int(await self._redis.zcard(keys[3])),
        }
