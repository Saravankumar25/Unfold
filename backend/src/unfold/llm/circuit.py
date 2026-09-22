"""Circuit breaker per `provider:model`, shared across workers through Redis (LLD §13, AI_infrastructure §9).

CLOSED → OPEN after `threshold` consecutive failures; OPEN for `open_seconds` → HALF_OPEN admitting exactly one
probe (the probe holds a lease so a crashed prober does not wedge the circuit); probe success → CLOSED,
probe failure → OPEN again.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from redis.asyncio import Redis


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# KEYS: hash   ARGV: open_ms, probe_lease_ms  → 1 allowed / 0 denied
_ALLOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'closed' then return 1 end
if state == 'open' then
  local opened = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or '0')
  if now - opened < tonumber(ARGV[1]) then return 0 end
  redis.call('HSET', KEYS[1], 'state', 'half_open', 'probe_until', now + tonumber(ARGV[2]))
  return 1
end
local probe_until = tonumber(redis.call('HGET', KEYS[1], 'probe_until') or '0')
if probe_until > now then return 0 end
redis.call('HSET', KEYS[1], 'probe_until', now + tonumber(ARGV[2]))
return 1
"""

# KEYS: hash   ARGV: threshold → new state
_FAILURE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
if state == 'half_open' or (state == 'closed' and failures >= tonumber(ARGV[1])) then
  redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now, 'probe_until', 0)
  state = 'open'
end
redis.call('PEXPIRE', KEYS[1], 86400000)
return state
"""

_SUCCESS = """
redis.call('HSET', KEYS[1], 'state', 'closed', 'failures', 0, 'probe_until', 0)
redis.call('PEXPIRE', KEYS[1], 86400000)
return 'closed'
"""

# KEYS: hash   ARGV: open_ms → effective state (reports HALF_OPEN once the open period elapsed)
_STATE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'open' then
  local opened = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or '0')
  if now - opened >= tonumber(ARGV[1]) then return 'half_open' end
end
return state
"""


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class CircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "llm",
        threshold: int = 5,
        open_seconds: float = 60.0,
        probe_lease_seconds: float = 150.0,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._threshold = threshold
        self._open_ms = int(open_seconds * 1000)
        self._probe_ms = int(probe_lease_seconds * 1000)
        self._allow = redis.register_script(_ALLOW)
        self._failure = redis.register_script(_FAILURE)
        self._success = redis.register_script(_SUCCESS)
        self._state = redis.register_script(_STATE)

    def _key(self, provider: str, model: str) -> str:
        return f"{self._prefix}:circuit:{provider}:{model}"

    async def allow(self, provider: str, model: str) -> bool:
        return (
            int(await self._allow(keys=[self._key(provider, model)], args=[self._open_ms, self._probe_ms]))
            == 1
        )

    async def record_success(self, provider: str, model: str) -> None:
        await self._success(keys=[self._key(provider, model)])

    async def record_failure(self, provider: str, model: str) -> CircuitState:
        return CircuitState(
            _text(await self._failure(keys=[self._key(provider, model)], args=[self._threshold]))
        )

    async def state(self, provider: str, model: str) -> CircuitState:
        return CircuitState(_text(await self._state(keys=[self._key(provider, model)], args=[self._open_ms])))
