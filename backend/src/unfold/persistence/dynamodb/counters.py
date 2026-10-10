"""Atomic counters and the rate limiter on DynamoDB (ADR 0016).

One `UpdateItem ADD` per call. A window is part of the key, so correctness never depends on item expiry: `ttl` is
set only so old windows are eventually removed (DynamoDB deletes "typically within a few days").
"""

from __future__ import annotations

import time
from typing import Any

from unfold.persistence.dynamodb.table import ConditionFailed, DynamoTable
from unfold.ports import CounterStore, RateLimiter

_SK = "C"


class DynamoCounters(CounterStore):
    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    async def add(
        self,
        key: str,
        amounts: dict[str, int],
        *,
        limits: dict[str, int] | None = None,
        ttl_seconds: int = 0,
    ) -> dict[str, int] | None:
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        adds: list[str] = []
        conds: list[str] = []
        for i, (attr, amount) in enumerate(amounts.items()):
            names[f"#a{i}"] = attr
            values[f":v{i}"] = amount
            adds.append(f"#a{i} :v{i}")
            limit = (limits or {}).get(attr)
            if limit is not None:
                if amount > limit:
                    return None
                values[f":m{i}"] = limit - amount  # the value the counter may hold *before* this add
                conds.append(f"(attribute_not_exists(#a{i}) OR #a{i} <= :m{i})")
        expression = "ADD " + ", ".join(adds)
        if ttl_seconds:
            names["#ttl"] = "ttl"
            values[":ttl"] = int(time.time()) + ttl_seconds
            expression += " SET #ttl = :ttl"
        try:
            item = await self._table.update(
                f"C#{key}",
                _SK,
                expression,
                names=names,
                values=values,
                condition=" AND ".join(conds) or None,
            )
        except ConditionFailed:
            return None
        return {attr: int(item.get(attr, 0)) for attr in amounts}

    async def get(self, key: str) -> dict[str, int]:
        item = await self._table.get(f"C#{key}", _SK)
        if item is None:
            return {}
        return {k: int(v) for k, v in item.items() if k not in ("PK", "SK", "ttl")}


class DynamoRateLimiter(RateLimiter):
    """Fixed window; counts denied attempts too (the Redis limiter's `INCR` semantics)."""

    def __init__(self, counters: CounterStore, table: DynamoTable) -> None:
        self._counters = counters
        self._table = table

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        start = int(time.time()) // window_seconds * window_seconds
        new = await self._counters.add(f"Q#{key}#{start}", {"n": 1}, ttl_seconds=window_seconds * 2 + 3600)
        assert new is not None  # unconditional add
        return new["n"] <= limit

    async def delete_prefix(self, prefix: str) -> int:
        # Keys carry a session id or an address, never project data, and every window expires on its own.
        return 0

    async def ping(self) -> None:
        await self._table.get("PING", "PING", consistent=False)
