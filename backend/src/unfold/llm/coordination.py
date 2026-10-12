"""What the gateway needs from its coordination layer, as protocols (ADR 0016).

Two implementations exist. `budget`/`circuit`/`key_pool`/`usage` keep this state in Redis (local stack, shared by
every process). `counter_coordination` keeps the cross-process parts (per-job usage, provider windows) in the
`CounterStore` port and the rest in process, which is correct for one-shot workers and independent Lambda
instances. Nothing here imports a store SDK.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from unfold.domain.enums import Priority
from unfold.llm.schemas import LLMRequest

WAIT_BUDGET_S: dict[Priority, float] = {
    Priority.P0: 20.0,
    Priority.P1: 180.0,
    Priority.P2: 60.0,
    Priority.P3: 30.0,
}
CONCURRENCY_WAIT_S = 8.0  # how long a full concurrency limit is polled before giving up on a model


@dataclass(frozen=True)
class Reservation:
    id: str
    model: str
    scope: str
    est_tokens: int
    ts_ms: int
    day: int
    key_index: int | None = None


class BudgetPort(Protocol):
    async def try_reserve(
        self, model: str, est_tokens: int, *, key_index: int | None = None
    ) -> tuple[Reservation | None, float, str]: ...
    async def reserve(
        self,
        model: str,
        est_tokens: int,
        priority: Priority,
        *,
        key_index: int | None = None,
        max_wait_s: float | None = None,
    ) -> Reservation | None: ...
    async def commit(self, reservation: Reservation, actual_tokens: int | None = None) -> None: ...
    async def release(self, reservation: Reservation) -> None: ...
    async def cooldown(self, model: str, seconds: float, *, key_index: int | None = None) -> None: ...


class ReservingBudget:
    """The wait loop shared by every budget: poll `try_reserve` within the priority's wait budget.

    Gives up immediately when the server hint (cooldown / daily quota / window) exceeds the remaining wait; a
    full concurrency limit is polled instead because its hint is only the lease ceiling.
    """

    _step: float = 1.0

    async def try_reserve(
        self, model: str, est_tokens: int, *, key_index: int | None = None
    ) -> tuple[Reservation | None, float, str]:
        raise NotImplementedError

    async def reserve(
        self,
        model: str,
        est_tokens: int,
        priority: Priority,
        *,
        key_index: int | None = None,
        max_wait_s: float | None = None,
    ) -> Reservation | None:
        budget = WAIT_BUDGET_S[priority] if max_wait_s is None else max(0.0, max_wait_s)
        deadline = time.monotonic() + budget
        concurrency_deadline = time.monotonic() + CONCURRENCY_WAIT_S
        while True:
            res, hint, reason = await self.try_reserve(model, est_tokens, key_index=key_index)
            if res is not None:
                return res
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if reason == "concurrency":
                # In-flight calls normally finish far sooner than the lease, so poll briefly; past that the
                # caller should try another candidate rather than queue behind this one.
                if time.monotonic() >= concurrency_deadline:
                    return None
                await asyncio.sleep(max(0.01, min(self._step, remaining)))
                continue
            if hint > remaining:
                return None
            await asyncio.sleep(max(0.01, min(self._step, hint, remaining)))


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitPort(Protocol):
    async def allow(self, provider: str, model: str) -> bool: ...
    async def record_success(self, provider: str, model: str) -> None: ...
    async def record_failure(self, provider: str, model: str) -> Any: ...


class KeyPoolPort(Protocol):
    async def acquire(self, provider: str, key_count: int) -> int | None: ...
    async def record_success(self, provider: str, index: int) -> None: ...
    async def record_failure(self, provider: str, index: int) -> None: ...
    async def record_rate_limited(
        self, provider: str, index: int, cooldown_s: float | None = None
    ) -> None: ...
    async def disable(self, provider: str, index: int) -> None: ...


class CachePort(Protocol):
    async def get(self, req: LLMRequest, kind: str = "text") -> Any | None: ...
    async def set(self, req: LLMRequest, value: Any, kind: str = "text") -> None: ...


class UsageStore(Protocol):
    """Per-job call and token accounting; the admission check is the per-job cost cap."""

    async def admit(self, job_id: str, *, soft: int, hard: int, priority: int, tokens: int) -> str | None:
        """Count one call. None = admitted; otherwise the limit that refused it: 'hard', 'tokens' or 'soft'."""
        ...

    async def reject(self, job_id: str) -> None: ...
    async def add(self, job_id: str, increments: dict[str, int]) -> None: ...
    async def read(self, job_id: str) -> dict[str, int]: ...
