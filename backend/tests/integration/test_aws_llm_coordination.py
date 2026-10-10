"""LLM coordination without Redis (ADR 0016): per-job cap, provider windows, circuit, key rotation.

The counter store is the DynamoDB adapter over in-process AWS, so the conditional adds are the real expressions.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.domain.enums import Priority
from unfold.llm.coordination import CircuitState
from unfold.llm.counter_coordination import (
    CounterKeyPool,
    CounterProviderBudget,
    CounterUsageStore,
    LocalCircuitBreaker,
)
from unfold.llm.factory import build_gateway
from unfold.llm.schemas import LLMRequest, Message, ModelCapability
from unfold.persistence.dynamodb.counters import DynamoCounters
from unfold.persistence.dynamodb.table import DynamoTable


@pytest.fixture
def counters(aws: Any) -> DynamoCounters:
    return DynamoCounters(DynamoTable(aws.settings))


async def test_job_call_cap_is_exact_under_concurrency(counters: DynamoCounters) -> None:
    usage = CounterUsageStore(counters)
    job = str(uuid.uuid4())
    results = await asyncio.gather(
        *(usage.admit(job, soft=100, hard=7, priority=0, tokens=1_000_000) for _ in range(25))
    )
    assert results.count(None) == 7 and results.count("hard") == 18


async def test_token_and_soft_caps(counters: DynamoCounters) -> None:
    usage = CounterUsageStore(counters)
    job = str(uuid.uuid4())
    await usage.add(job, {"input_tokens": 600, "output_tokens": 500})
    assert await usage.admit(job, soft=10, hard=13, priority=0, tokens=1000) == "tokens"
    other = str(uuid.uuid4())
    for _ in range(3):
        assert await usage.admit(other, soft=3, hard=9, priority=3, tokens=10**9) is None
    assert (
        await usage.admit(other, soft=3, hard=9, priority=3, tokens=10**9) == "soft"
    )  # low priority stops first
    assert (
        await usage.admit(other, soft=3, hard=9, priority=0, tokens=10**9) is None
    )  # the important call goes on


async def test_gateway_budget_exhaustion_is_the_typed_error(aws: Any) -> None:
    from unfold.persistence.dynamodb.counters import DynamoCounters as C

    settings = aws.settings.model_copy(update={"analysis_llm_call_budget": 1})
    gateway = build_gateway(settings, None, counters=C(DynamoTable(settings)))
    req = LLMRequest(
        purpose="t",
        capability=ModelCapability.FAST,
        priority=Priority.P0,
        system="s",
        messages=[Message(role="user", content="hi")],
        job_id=str(uuid.uuid4()),
    )
    await gateway._admit(req)  # soft 1 -> hard ceil(1.3) = 2
    await gateway._admit(req)
    with pytest.raises(UnfoldError) as exc:
        await gateway._admit(req)
    assert exc.value.code is ErrorCode.LLM_BUDGET_EXHAUSTED
    usage = await gateway.usage(req.job_id or "")
    assert usage["calls"] == 2 and usage["budget_rejections"] == 1


async def test_provider_windows_and_slots(aws: Any, counters: DynamoCounters) -> None:
    settings = aws.settings.model_copy(
        update={"llm_model_limits": '{"m": {"rpm": 3, "tpm": 1000, "rpd": 100, "concurrency": 2}}'}
    )
    budget = CounterProviderBudget(settings, counters)
    a, _, _ = await budget.try_reserve("m", 100)
    b, _, _ = await budget.try_reserve("m", 100)
    assert a is not None and b is not None
    full, hint, reason = await budget.try_reserve("m", 100)
    assert full is None and reason == "concurrency"  # two slots in flight
    await budget.commit(a, 40)  # the slot is freed, the estimate is replaced by the real usage
    c, _, _ = await budget.try_reserve("m", 100)
    assert c is not None
    await budget.release(b)  # never reached the provider: the request and tokens are given back
    await budget.commit(c, 100)
    d, _, _ = await budget.try_reserve(
        "m", 100
    )  # the third request in the window: allowed (rpm 3 incl. release)
    assert d is not None
    await budget.commit(d, 100)
    e, hint, reason = await budget.try_reserve("m", 100)
    assert e is None and reason == "window" and 0 < hint <= 60


async def test_day_limit_and_oversized_request(aws: Any, counters: DynamoCounters) -> None:
    settings = aws.settings.model_copy(
        update={"llm_model_limits": '{"d": {"rpm": 50, "tpm": 100, "rpd": 2, "concurrency": 9}}'}
    )
    budget = CounterProviderBudget(settings, counters)
    big, _, _ = await budget.try_reserve("d", 5000)  # larger than the whole window: allowed while it is alone
    assert big is not None
    blocked, _, reason = await budget.try_reserve("d", 10)  # the window is full of the big estimate
    assert blocked is None and reason == "window"
    await budget.commit(big, 20)  # actual usage replaces the estimate
    second, _, _ = await budget.try_reserve("d", 10)
    assert second is not None
    await budget.commit(second, 10)
    third, hint, reason = await budget.try_reserve("d", 10)  # two requests today: the daily cap
    assert third is None and reason == "rpd" and hint > 0


async def test_cooldown_blocks_until_it_passes(aws: Any, counters: DynamoCounters) -> None:
    budget = CounterProviderBudget(aws.settings, counters)
    await budget.cooldown("x", 30)
    res, hint, reason = await budget.try_reserve("x", 10)
    assert res is None and reason == "cooldown" and 0 < hint <= 30
    assert await budget.reserve("x", 10, Priority.P0, max_wait_s=0.0) is None


async def test_circuit_breaker_state_machine() -> None:
    cb = LocalCircuitBreaker(threshold=3, open_seconds=0.05, probe_lease_seconds=0.05)
    for _ in range(2):
        assert await cb.record_failure("p", "m") is CircuitState.CLOSED
    assert await cb.record_failure("p", "m") is CircuitState.OPEN
    assert not await cb.allow("p", "m")
    await asyncio.sleep(0.06)
    assert await cb.allow("p", "m")  # one probe
    assert not await cb.allow("p", "m")  # the probe holds the lease
    assert await cb.record_failure("p", "m") is CircuitState.OPEN  # a failed probe re-opens
    await asyncio.sleep(0.06)
    assert await cb.allow("p", "m")
    await cb.record_success("p", "m")
    assert await cb.allow("p", "m") and await cb.allow("p", "m")


async def test_key_pool_rotates_and_persists_disablement(counters: DynamoCounters) -> None:
    pool = CounterKeyPool(counters, disable_seconds=3600)
    first = await pool.acquire("gemini", 3)
    second = await pool.acquire("gemini", 3)
    assert {first, second} <= {0, 1, 2} and first != second  # least recently used
    await pool.disable("gemini", 0)
    picks = {await pool.acquire("gemini", 3) for _ in range(6)}
    assert 0 not in picks
    # a new process (cold start) still sees the rejected key as disabled
    fresh = CounterKeyPool(counters)
    assert 0 not in {await fresh.acquire("gemini", 3) for _ in range(6)}
    await pool.record_rate_limited("gemini", 1, cooldown_s=30)
    assert 1 not in {await pool.acquire("gemini", 3) for _ in range(6)}
    only = CounterKeyPool(counters)
    await only.disable("groq", 0)
    assert await only.acquire("groq", 1) is None  # every key disabled: the caller falls to another provider
