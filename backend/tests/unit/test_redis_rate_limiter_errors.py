"""RedisRateLimiter wraps redis failures in the typed StoreUnavailable (audit P1-e), like MinioBlobStore."""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from unfold.core.errors import ErrorCode, StoreUnavailable
from unfold.persistence.redis_store import RedisRateLimiter


class _RaisingClient:
    def register_script(self, _script: str) -> Any:
        async def run(**_: Any) -> int:
            raise RedisConnectionError("connection refused")

        return run


class _Store:
    client = _RaisingClient()


async def test_hit_raises_store_unavailable_when_redis_fails() -> None:
    limiter = RedisRateLimiter(_Store())  # type: ignore[arg-type]
    with pytest.raises(StoreUnavailable) as info:
        await limiter.hit("k", 1, 60)
    assert info.value.code is ErrorCode.STORE_UNAVAILABLE
    assert isinstance(info.value.__cause__, RedisConnectionError)
