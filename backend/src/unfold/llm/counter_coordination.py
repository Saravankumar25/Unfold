"""Gateway coordination without Redis (ADR 0016), for the AWS profile.

Cross-process state goes through the `CounterStore` port (atomic conditional adds): the per-job cost cap, which
must survive attempts, and the per-model minute and day windows, which Lambda instances and workers share.
Concurrency slots, cooldowns, the circuit breaker and key rotation are in process: a one-shot worker serves one
job and chat instances are independent, so a shared copy would only add latency. Provider windows are fixed
(approximate) instead of the sliding windows Redis kept; key disablement is persisted so a rejected credential
stays out of rotation across cold starts.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from unfold.core.config import Settings
from unfold.llm.coordination import CircuitState, Reservation, ReservingBudget
from unfold.llm.schemas import LLMRequest
from unfold.ports import CounterStore

USAGE_TTL_SECONDS = 7 * 86_400
_DAY = 86_400
_KEY_REFRESH_S = 60.0


class CounterUsageStore:
    def __init__(self, counters: CounterStore, *, ttl_seconds: int = USAGE_TTL_SECONDS) -> None:
        self._c = counters
        self._ttl = ttl_seconds

    @staticmethod
    def _key(job_id: str) -> str:
        return f"U#{job_id}"

    async def admit(self, job_id: str, *, soft: int, hard: int, priority: int, tokens: int) -> str | None:
        cur = await self._c.get(self._key(job_id))
        if cur.get("input_tokens", 0) + cur.get("output_tokens", 0) >= tokens:
            return "tokens"
        if priority >= 2 and cur.get("calls", 0) >= soft:
            return "soft"
        # the hard call cap is the exact one: a conditional add, so concurrent callers cannot overshoot it
        ok = await self._c.add(self._key(job_id), {"calls": 1}, limits={"calls": hard}, ttl_seconds=self._ttl)
        return None if ok is not None else "hard"

    async def reject(self, job_id: str) -> None:
        await self._c.add(self._key(job_id), {"budget_rejections": 1}, ttl_seconds=self._ttl)

    async def add(self, job_id: str, increments: dict[str, int]) -> None:
        # one item per job; a handful of attributes (purpose, model and outcome breakdowns)
        if increments:
            await self._c.add(self._key(job_id), increments, ttl_seconds=self._ttl)

    async def read(self, job_id: str) -> dict[str, int]:
        return await self._c.get(self._key(job_id))


class CounterProviderBudget(ReservingBudget):
    """Per-model request, token and daily windows in the counter store; concurrency and cooldown in process."""

    def __init__(self, settings: Settings, counters: CounterStore, *, step_seconds: float = 1.0) -> None:
        self._settings = settings
        self._c = counters
        self._step = min(1.0, step_seconds)
        self._in_flight: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}

    def scope(self, model: str, key_index: int | None = None) -> str:
        if self._settings.llm_quota_scope == "per_key" and key_index is not None:
            return f"{model}:k{key_index}"
        return model

    async def try_reserve(
        self, model: str, est_tokens: int, *, key_index: int | None = None
    ) -> tuple[Reservation | None, float, str]:
        scope = self.scope(model, key_index)
        limits = self._settings.model_limits(model)
        est = max(0, int(est_tokens))
        now = time.time()
        cooling = self._cooldown_until.get(scope, 0.0) - time.monotonic()
        if cooling > 0:
            return None, cooling, "cooldown"
        if self._in_flight.get(scope, 0) >= int(limits.get("concurrency", 2)):
            return None, 1.0, "concurrency"
        minute, day = int(now // 60), int(now // _DAY)
        rpm, tpm = int(limits.get("rpm", 5)), int(limits.get("tpm", 200_000))
        # a request larger than the whole token window is let through when nothing else used it (as before)
        window = await self._c.add(
            f"L#{scope}#m{minute}",
            {"rpm": 1, "tpm": est},
            limits={"rpm": rpm, "tpm": max(tpm, est)},
            ttl_seconds=180,
        )
        if window is None:
            return None, 60.0 - now % 60.0, "window"
        daily = await self._c.add(
            f"L#{scope}#d{day}", {"rpd": 1}, limits={"rpd": int(limits.get("rpd", 100))}, ttl_seconds=2 * _DAY
        )
        if daily is None:
            await self._c.add(f"L#{scope}#m{minute}", {"rpm": -1, "tpm": -est})
            return None, float((day + 1) * _DAY) - now, "rpd"
        self._in_flight[scope] = self._in_flight.get(scope, 0) + 1
        return Reservation(uuid.uuid4().hex, model, scope, est, int(now * 1000), day, key_index), 0.0, "ok"

    async def commit(self, reservation: Reservation, actual_tokens: int | None = None) -> None:
        """The call reached the provider: keep the request counted, replace the token estimate, free the slot."""
        self._free(reservation)
        if actual_tokens is not None and actual_tokens != reservation.est_tokens:
            minute = reservation.ts_ms // 60_000
            await self._c.add(
                f"L#{reservation.scope}#m{minute}", {"tpm": max(0, actual_tokens) - reservation.est_tokens}
            )

    async def release(self, reservation: Reservation) -> None:
        """The call never reached the provider: undo the reservation entirely."""
        self._free(reservation)
        minute = reservation.ts_ms // 60_000
        await self._c.add(f"L#{reservation.scope}#m{minute}", {"rpm": -1, "tpm": -reservation.est_tokens})
        await self._c.add(f"L#{reservation.scope}#d{reservation.day}", {"rpd": -1})

    async def cooldown(self, model: str, seconds: float, *, key_index: int | None = None) -> None:
        scope = self.scope(model, key_index)
        until = time.monotonic() + max(1.0, seconds)
        self._cooldown_until[scope] = max(
            self._cooldown_until.get(scope, 0.0), until
        )  # extend, never shorten

    def _free(self, reservation: Reservation) -> None:
        n = self._in_flight.get(reservation.scope, 0)
        self._in_flight[reservation.scope] = max(0, n - 1)


class LocalCircuitBreaker:
    """CLOSED -> OPEN after `threshold` consecutive failures; OPEN for `open_seconds`, then HALF_OPEN admitting
    one probe at a time (the probe holds a lease so a crashed prober cannot wedge it)."""

    def __init__(
        self, *, threshold: int = 5, open_seconds: float = 60.0, probe_lease_seconds: float = 150.0
    ) -> None:
        self._threshold = threshold
        self._open_s = open_seconds
        self._lease_s = probe_lease_seconds
        self._st: dict[tuple[str, str], dict[str, Any]] = {}

    def _get(self, provider: str, model: str) -> dict[str, Any]:
        return self._st.setdefault(
            (provider, model),
            {"state": CircuitState.CLOSED, "failures": 0, "opened_at": 0.0, "probe_until": 0.0},
        )

    async def allow(self, provider: str, model: str) -> bool:
        s, now = self._get(provider, model), time.monotonic()
        if s["state"] is CircuitState.CLOSED:
            return True
        if s["state"] is CircuitState.OPEN:
            if now - s["opened_at"] < self._open_s:
                return False
            s["state"] = CircuitState.HALF_OPEN
        elif s["probe_until"] > now:
            return False
        s["probe_until"] = now + self._lease_s
        return True

    async def record_success(self, provider: str, model: str) -> None:
        s = self._get(provider, model)
        s.update(state=CircuitState.CLOSED, failures=0, probe_until=0.0)

    async def record_failure(self, provider: str, model: str) -> CircuitState:
        s = self._get(provider, model)
        s["failures"] += 1
        if s["state"] is CircuitState.HALF_OPEN or (
            s["state"] is CircuitState.CLOSED and s["failures"] >= self._threshold
        ):
            s.update(state=CircuitState.OPEN, opened_at=time.monotonic(), probe_until=0.0)
        state: CircuitState = s["state"]
        return state


class CounterKeyPool:
    """Least-recently-used healthy key per provider. Health is in process except `disabled_until`, which is
    persisted so a rejected key stays out of rotation across cold starts (read at most once a minute)."""

    def __init__(self, counters: CounterStore, *, disable_seconds: float = 3600.0) -> None:
        self._c = counters
        self._disable_s = disable_seconds
        self._last_used: dict[tuple[str, int], float] = {}
        self._cooling: dict[tuple[str, int], float] = {}
        self._disabled_until: dict[tuple[str, int], float] = {}
        self._checked: dict[tuple[str, int], float] = {}

    @staticmethod
    def _store_key(provider: str, index: int) -> str:
        return f"K#{provider}#{index}"

    async def _persisted_disabled_until(self, provider: str, index: int) -> float:
        k, now = (provider, index), time.monotonic()
        if now - self._checked.get(k, -_KEY_REFRESH_S) >= _KEY_REFRESH_S:
            self._checked[k] = now
            until = (await self._c.get(self._store_key(provider, index))).get("until", 0)
            self._disabled_until[k] = max(self._disabled_until.get(k, 0.0), float(until))
        return self._disabled_until.get(k, 0.0)

    async def acquire(self, provider: str, key_count: int) -> int | None:
        best: int | None = None
        best_used = float("inf")
        now, wall = time.monotonic(), time.time()
        for i in range(max(0, key_count)):
            if await self._persisted_disabled_until(provider, i) > wall:
                continue
            if self._cooling.get((provider, i), 0.0) > now:
                continue
            used = self._last_used.get((provider, i), 0.0)
            if used < best_used:
                best, best_used = i, used
        if best is not None:
            self._last_used[(provider, best)] = now
        return best

    async def record_success(self, provider: str, index: int) -> None:
        return None

    async def record_failure(self, provider: str, index: int) -> None:
        return None

    async def record_rate_limited(self, provider: str, index: int, cooldown_s: float | None = None) -> None:
        if cooldown_s is not None and cooldown_s > 0:
            self._cooling[(provider, index)] = time.monotonic() + cooldown_s

    async def disable(self, provider: str, index: int) -> None:
        """Credential rejected: out of rotation for `disable_seconds` (the counter store keeps the deadline)."""
        until = time.time() + self._disable_s
        self._disabled_until[(provider, index)] = until
        persisted = (await self._c.get(self._store_key(provider, index))).get("until", 0)
        if until > persisted:
            await self._c.add(
                self._store_key(provider, index),
                {"until": int(until) - int(persisted)},
                ttl_seconds=int(self._disable_s) + _DAY,
            )


class NoCache:
    """The response cache is a Redis-only optimisation; the AWS profile runs without it (ADR 0016)."""

    async def get(self, req: LLMRequest, kind: str = "text") -> Any | None:
        return None

    async def set(self, req: LLMRequest, value: Any, kind: str = "text") -> None:
        return None
