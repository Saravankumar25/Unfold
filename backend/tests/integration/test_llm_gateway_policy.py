"""LLM gateway policy against real Redis with scripted fake providers (test doubles live only here).

Each test uses its own Redis key prefix, so budgets / circuits / key health never leak between tests or into the
development data, and everything is deleted afterwards.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, GenerationError, ProviderCapacityError, ProviderError, UnfoldError
from unfold.domain.enums import ModelCapability, Priority
from unfold.llm.budget import ProviderBudget
from unfold.llm.circuit import CircuitBreaker, CircuitState
from unfold.llm.gateway import LLMGateway
from unfold.llm.key_pool import KeyPool
from unfold.llm.providers.base import (
    AuthError,
    BadRequest,
    InvalidOutput,
    RateLimited,
    TransientError,
    tier_models,
)
from unfold.llm.schemas import LLMRequest, LLMResponse, Message, ToolCall, ToolSpec, Usage
from unfold.runtime import Runtime
from unfold.security.secrets import REDACTED

pytestmark = pytest.mark.integration

GENEROUS = {"*": {"rpm": 10_000, "tpm": 100_000_000, "rpd": 1_000_000, "concurrency": 50}}

# An action is: an exception instance (raised), a callable(key_index) -> action, or None (default success).
Action = Any


class FakeProvider:
    """Scripted `Provider`: per-model queues of actions; records every call it receives."""

    def __init__(
        self, name: str, tiers: dict[str, str | None], *, keys: int = 2, long_context: bool = True
    ) -> None:
        self.name = name
        self._tiers = tiers
        self._long_context = long_context
        self._keys = keys
        self.script: dict[str, list[Action]] = defaultdict(list)
        self.json_script: dict[str, list[Action]] = defaultdict(list)
        self.stream_script: dict[str, list[Action]] = defaultdict(list)
        self.calls: list[tuple[str, int, LLMRequest]] = []

    @property
    def key_count(self) -> int:
        return self._keys

    def models_for(self, capability: ModelCapability) -> list[str]:
        if capability is ModelCapability.LONG_CONTEXT and not self._long_context:
            return []  # mirrors GroqProvider: small windows, no long-context tier
        return tier_models(self._tiers, capability)

    def models_called(self) -> list[str]:
        return [m for m, _, _ in self.calls]

    @staticmethod
    def _next(queue: list[Action], key_index: int) -> Action:
        action = queue.pop(0) if queue else None
        while callable(action) and not isinstance(action, BaseException):
            action = action(key_index)
        return action

    async def generate(self, model: str, key_index: int, req: LLMRequest) -> LLMResponse:
        self.calls.append((model, key_index, req))
        action = self._next(self.script[model], key_index)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, LLMResponse):
            return action
        return LLMResponse(
            text=f"ok:{model}:{key_index}",
            provider=self.name,
            model=model,
            usage=Usage(input_tokens=11, output_tokens=7),
        )

    async def generate_json(
        self, model: str, key_index: int, req: LLMRequest, json_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], LLMResponse]:
        self.calls.append((model, key_index, req))
        action = self._next(self.json_script[model], key_index)
        if isinstance(action, BaseException):
            raise action
        data = action if isinstance(action, dict) else {"answer": f"from {model}", "score": 3}
        resp = LLMResponse(
            text=json.dumps(data),
            provider=self.name,
            model=model,
            usage=Usage(input_tokens=5, output_tokens=5),
        )
        return data, resp

    async def stream(self, model: str, key_index: int, req: LLMRequest) -> AsyncIterator[str]:
        self.calls.append((model, key_index, req))
        action = self._next(self.stream_script[model], key_index)
        if isinstance(action, BaseException):
            raise action
        for item in action if isinstance(action, list) else ["he", "llo"]:
            if isinstance(item, BaseException):
                raise item
            await asyncio.sleep(0)
            yield item


def _gemini() -> FakeProvider:
    return FakeProvider("gemini", {"fast": "g-fast", "balanced": "g-bal", "reasoning": "g-reason"})


def _groq() -> FakeProvider:
    return FakeProvider(
        "groq", {"fast": "q-fast", "balanced": "q-bal", "reasoning": "q-bal"}, long_context=False
    )


def _req(capability: ModelCapability = ModelCapability.FAST, **kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "purpose": "test.policy",
        "capability": capability,
        "priority": Priority.P0,
        "messages": [Message(role="user", content="hello")],
        "max_output_tokens": 64,
    }
    base.update(kw)
    return LLMRequest(**base)


class Harness:
    def __init__(self, runtime: Runtime, settings: Settings, prefix: str) -> None:
        self.runtime = runtime
        self.settings = settings
        self.prefix = prefix

    def gateway(
        self,
        *providers: FakeProvider,
        settings: Settings | None = None,
        circuit: CircuitBreaker | None = None,
    ) -> LLMGateway:
        return LLMGateway(
            settings or self.settings,
            self.runtime.redis,
            {p.name: p for p in providers},
            prefix=self.prefix,
            circuit=circuit,
            backoff_base_s=0.001,
        )

    def settings_with(self, **update: Any) -> Settings:
        return self.settings.model_copy(update=update)


@pytest.fixture
async def h(runtime: Runtime, settings: Settings) -> AsyncIterator[Harness]:
    prefix = f"llmtest:{uuid.uuid4().hex[:12]}"
    harness = Harness(runtime, settings.model_copy(update={"llm_model_limits": json.dumps(GENEROUS)}), prefix)
    yield harness
    await runtime.redis.delete_prefix(prefix)


# --- routing / fallback -------------------------------------------------------------------------------------


async def test_fallback_order_follows_capability_tiers_then_groq(h: Harness) -> None:
    gem, groq = _gemini(), _groq()
    gw = h.gateway(gem, groq)
    assert [c.label for c in gw.router.candidates(ModelCapability.REASONING)] == [
        "gemini:g-reason",
        "gemini:g-bal",
        "groq:q-bal",
    ]
    # FAST → [fast, balanced]: a 404 on the fast model falls through to balanced, then a 5xx storm to groq.
    gem.script["g-fast"] = [BadRequest("unknown model", model_specific=True, status=404)]
    gem.script["g-bal"] = [TransientError("503")] * 3
    resp = await gw.generate(_req(ModelCapability.FAST))
    assert (resp.provider, resp.model) == ("groq", "q-fast")
    assert gem.models_called() == ["g-fast", "g-bal", "g-bal", "g-bal"]
    assert groq.models_called() == ["q-fast"]


async def test_bad_request_is_not_retried_elsewhere(h: Harness) -> None:
    gem, groq = _gemini(), _groq()
    gem.script["g-fast"] = [BadRequest("bad schema")]
    with pytest.raises(GenerationError) as ei:
        await h.gateway(gem, groq).generate(_req())
    assert ei.value.code is ErrorCode.GENERATION_FAILED
    assert gem.models_called() == ["g-fast"]
    assert groq.calls == []


async def test_context_length_only_continues_on_long_context_candidates(h: Harness) -> None:
    gem, groq = _gemini(), _groq()
    # FAST candidates: g-fast, g-bal, q-fast, q-bal. LONG_CONTEXT (gemini only) = {g-bal, g-reason}.
    gem.script["g-fast"] = [BadRequest("too long", context_length=True)]
    gem.script["g-bal"] = [TransientError("503")] * 3
    with pytest.raises(ProviderCapacityError):
        await h.gateway(gem, groq).generate(_req())
    assert gem.models_called() == ["g-fast", "g-bal", "g-bal", "g-bal"]
    assert groq.calls == []  # groq has no long-context tier


async def test_not_configured_gateway_raises_typed_error(h: Harness) -> None:
    gw = h.gateway(FakeProvider("gemini", {"fast": "g-fast"}, keys=0))
    with pytest.raises(ProviderError) as ei:
        await gw.generate(_req())
    assert ei.value.code is ErrorCode.PROVIDER_NOT_CONFIGURED


# --- 429 / auth / 5xx --------------------------------------------------------------------------------------


async def test_rate_limit_short_retry_after_retries_same_model_after_cooldown(h: Harness) -> None:
    gem = _gemini()
    gem.script["g-fast"] = [RateLimited("429", retry_after_s=0.4)]
    gw = h.gateway(gem)
    started = time.monotonic()
    resp = await gw.generate(_req(job_id="job-rl"))
    assert resp.model == "g-fast"
    assert gem.models_called() == ["g-fast", "g-fast"]
    assert time.monotonic() - started >= 0.35  # waited for the cooldown before retrying
    usage = await gw.usage("job-rl")
    assert usage["rate_limited"] == 1
    assert usage["retries"] == 1
    health = await KeyPool(h.runtime.redis.client, prefix=h.prefix).health("gemini", 2)
    assert any(k["last_429"] for k in health)


async def test_rate_limit_long_retry_after_falls_back_and_sets_model_cooldown(h: Harness) -> None:
    gem = _gemini()
    gem.script["g-fast"] = [RateLimited("429", retry_after_s=300)]
    gw = h.gateway(gem)
    started = time.monotonic()
    resp = await gw.generate(_req())
    assert resp.model == "g-bal"
    assert time.monotonic() - started < 5
    budget = ProviderBudget(h.settings, h.runtime.redis.client, prefix=h.prefix)
    assert 250 < await budget.cooldown_remaining("g-fast") <= 300
    # While cooling down, the next call goes straight to the fallback model.
    await gw.generate(_req())
    assert gem.models_called() == ["g-fast", "g-bal", "g-bal"]


async def test_auth_error_disables_key_and_uses_the_next_one(h: Harness) -> None:
    gem = _gemini()
    gem.script["g-fast"] = [lambda k: AuthError("401") if k == 0 else None]
    gw = h.gateway(gem)
    # LRU selection on a fresh pool picks key 0 first.
    resp = await gw.generate(_req())
    assert resp.text == "ok:g-fast:1"
    assert [(m, k) for m, k, _ in gem.calls] == [("g-fast", 0), ("g-fast", 1)]
    health = await KeyPool(h.runtime.redis.client, prefix=h.prefix).health("gemini", 2)
    assert health[0]["disabled"] is True and health[0]["healthy"] is False
    assert health[1]["healthy"] is True
    for _ in range(3):
        await gw.generate(_req())
    assert {k for _, k, _ in gem.calls[2:]} == {1}  # the disabled key never comes back in rotation


async def test_all_keys_rejected_skips_provider(h: Harness) -> None:
    gem, groq = _gemini(), _groq()
    gem.script["g-fast"] = [AuthError("401"), AuthError("401")]
    resp = await h.gateway(gem, groq).generate(_req())
    assert resp.provider == "groq"
    assert [(m, k) for m, k, _ in gem.calls] == [("g-fast", 0), ("g-fast", 1)]


async def test_transient_retry_then_circuit_open_then_half_open_probe(h: Harness) -> None:
    gem = _gemini()
    circuit = CircuitBreaker(h.runtime.redis.client, prefix=h.prefix, threshold=1, open_seconds=1.0)
    gw = h.gateway(gem, circuit=circuit)
    gem.script["g-fast"] = [TransientError("503")] * 3
    first = await gw.generate(_req(job_id="job-cb"))
    assert first.model == "g-bal"
    assert gem.models_called() == ["g-fast"] * 3 + ["g-bal"]  # 1 try + 2 retries, then fallback
    assert await circuit.state("gemini", "g-fast") is CircuitState.OPEN

    second = await gw.generate(_req())
    assert second.model == "g-bal"
    assert gem.models_called()[-1] == "g-bal" and gem.models_called().count("g-fast") == 3  # skipped

    await asyncio.sleep(1.1)
    assert await circuit.state("gemini", "g-fast") is CircuitState.HALF_OPEN
    third = await gw.generate(_req())  # the probe succeeds
    assert third.model == "g-fast"
    assert await circuit.state("gemini", "g-fast") is CircuitState.CLOSED
    usage = await gw.usage("job-cb")
    assert usage["retries"] == 2


async def test_capacity_exhausted_is_retryable_with_attempt_detail(h: Harness) -> None:
    gem = _gemini()
    gem.script["g-fast"] = [TransientError("503")] * 3
    gem.script["g-bal"] = [RateLimited("429", retry_after_s=500)]
    with pytest.raises(ProviderCapacityError) as ei:
        await h.gateway(gem).generate(_req())
    assert ei.value.retryable is True
    reasons = [a["reason"] for a in ei.value.detail["attempts"]]
    assert reasons == ["transient", "rate_limited"]


# --- structured output ---------------------------------------------------------------------------------------


class Verdict(BaseModel):
    answer: str
    score: int


async def test_structured_output_repair_feeds_error_back_once(h: Harness) -> None:
    gem = _gemini()
    gem.json_script["g-bal"] = [{"answer": "x", "score": "not-a-number"}]
    value = await h.gateway(gem).generate_structured(_req(ModelCapability.STRUCTURED), Verdict)
    assert value == Verdict(answer="from g-bal", score=3)
    assert gem.models_called() == ["g-bal", "g-bal"]
    repaired = gem.calls[1][2]
    assert [m.role for m in repaired.messages] == ["user", "assistant", "user"]
    assert "score" in repaired.messages[-1].content and "rejected" in repaired.messages[-1].content
    assert "not-a-number" in repaired.messages[1].content  # the invalid output is shown back to the model


async def test_structured_output_invalid_everywhere(h: Harness) -> None:
    gem = _gemini()
    gem.json_script["g-bal"] = [InvalidOutput("not json", raw="{"), InvalidOutput("not json", raw="{")]
    gem.json_script["g-fast"] = [{"nope": 1}, {"nope": 2}]
    with pytest.raises(GenerationError) as ei:
        await h.gateway(gem).generate_structured(_req(ModelCapability.STRUCTURED), Verdict)
    assert ei.value.code is ErrorCode.INVALID_MODEL_OUTPUT
    assert gem.models_called() == ["g-bal", "g-bal", "g-fast", "g-fast"]


# --- per-job call budget -------------------------------------------------------------------------------------


async def test_job_budget_skips_p2_beyond_soft_limit_and_hard_stops_at_1_3x(h: Harness) -> None:
    gem = _gemini()
    gw = h.gateway(gem, settings=h.settings_with(analysis_llm_call_budget=2))  # hard = ceil(2.6) = 3
    job = f"job-{uuid.uuid4().hex[:8]}"
    await gw.generate(_req(job_id=job, priority=Priority.P2))
    await gw.generate(_req(job_id=job, priority=Priority.P2))
    with pytest.raises(UnfoldError) as soft:
        await gw.generate(_req(job_id=job, priority=Priority.P2))
    assert soft.value.code is ErrorCode.LLM_BUDGET_EXHAUSTED and soft.value.detail["limit"] == "soft"
    await gw.generate(_req(job_id=job, priority=Priority.P1))  # core work may use the 30 % headroom
    with pytest.raises(UnfoldError) as hard:
        await gw.generate(_req(job_id=job, priority=Priority.P0))
    assert hard.value.detail["limit"] == "hard"
    assert len(gem.calls) == 3  # rejected calls never reach a provider
    usage = await gw.usage(job)
    assert usage["calls"] == 3
    assert usage["budget_rejections"] == 2
    assert usage["by_purpose"]["test.policy"]["calls"] == 3
    assert usage["by_model"]["g-fast"]["calls"] == 3
    assert usage["input_tokens"] == 33 and usage["output_tokens"] == 21


async def test_job_token_budget_refuses_further_calls_once_reached(h: Harness) -> None:
    gem = _gemini()
    # one fake call records 11 input + 7 output tokens
    gw = h.gateway(gem, settings=h.settings_with(analysis_llm_token_budget=18))
    job = f"job-{uuid.uuid4().hex[:8]}"
    await gw.generate(_req(job_id=job, priority=Priority.P0))
    with pytest.raises(UnfoldError) as hit:
        await gw.generate(_req(job_id=job, priority=Priority.P0))
    assert hit.value.code is ErrorCode.LLM_BUDGET_EXHAUSTED and hit.value.detail["limit"] == "tokens"
    assert len(gem.calls) == 1  # refused before any provider call
    await gw.generate(_req(job_id=f"job-{uuid.uuid4().hex[:8]}"))  # other jobs have their own budget
    assert len(gem.calls) == 2


async def test_requests_without_job_are_not_budgeted(h: Harness) -> None:
    gem = _gemini()
    gw = h.gateway(gem, settings=h.settings_with(analysis_llm_call_budget=0))
    for _ in range(3):
        await gw.generate(_req(priority=Priority.P2))
    assert len(gem.calls) == 3


# --- redaction / cache / streaming --------------------------------------------------------------------------

AWS = "AKIA" + "IOSFODNN7ABCDEFG"
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
STRIPE = "sk_live_" + "Z9y8X7w6V5u4T3s2R1q0"


def _secret_request() -> LLMRequest:
    return _req(
        system=f"system has {AWS}",
        messages=[
            Message(role="user", content=f"config: password = 'hunter2hunter2' and {GH}"),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="c1", name="read", arguments={"q": STRIPE, "nested": [{"t": AWS}]})],
            ),
            Message(role="tool", tool_call_id="c1", name="read", content=f"file text {STRIPE}"),
        ],
        tools=[ToolSpec(name="read", description=f"reads {GH}", parameters={"type": "object"})],
    )


def _all_text(req: LLMRequest) -> str:
    return req.model_dump_json()


async def test_every_outgoing_part_is_redacted(h: Harness) -> None:
    gem = _gemini()
    await h.gateway(gem).generate(_secret_request())
    sent = _all_text(gem.calls[0][2])
    for secret in (AWS, GH, STRIPE, "hunter2hunter2"):
        assert secret not in sent
    assert sent.count(REDACTED) >= 6


async def test_repair_prompt_and_stream_are_redacted_too(h: Harness) -> None:
    gem = _gemini()
    gem.json_script["g-bal"] = [InvalidOutput(f"bad {AWS}", raw=f'{{"leak": "{GH}"}}')]
    await h.gateway(gem).generate_structured(_req(ModelCapability.STRUCTURED, system=AWS), Verdict)
    assert all(AWS not in _all_text(r) and GH not in _all_text(r) for _, _, r in gem.calls)
    stream_gem = _gemini()
    chunks = [c async for c in h.gateway(stream_gem).stream(_secret_request())]
    assert "".join(chunks) == "hello"
    assert AWS not in _all_text(stream_gem.calls[0][2])


async def test_cacheable_requests_hit_the_response_cache(h: Harness) -> None:
    gem = _gemini()
    gw = h.gateway(gem)
    req = _req(cacheable=True, job_id="job-cache")
    a = await gw.generate(req)
    b = await gw.generate(req.model_copy(update={"job_id": "job-cache", "priority": Priority.P2}))
    assert a.cached is False and b.cached is True and b.text == a.text
    assert len(gem.calls) == 1
    s1 = await gw.generate_structured(req, Verdict)
    s2 = await gw.generate_structured(req, Verdict)
    assert s1 == s2 and len(gem.calls) == 2  # structured output has its own cache entry
    usage = await gw.usage("job-cache")
    assert usage["cache_hits"] == 2


async def test_stream_falls_back_before_first_token(h: Harness) -> None:
    gem = _gemini()
    gem.stream_script["g-bal"] = [TransientError("503")] * 3
    chunks = [c async for c in h.gateway(gem).stream(_req(ModelCapability.BALANCED, job_id="job-st"))]
    assert chunks == ["he", "llo"]
    assert gem.models_called() == ["g-bal"] * 3 + ["g-reason"]


async def test_stream_interrupted_after_first_token_raises(h: Harness) -> None:
    gem = _gemini()
    gem.stream_script["g-bal"] = [["first", TransientError("reset")]]
    got: list[str] = []
    with pytest.raises(ProviderError) as ei:
        async for c in h.gateway(gem).stream(_req(ModelCapability.BALANCED)):
            got.append(c)
    assert got == ["first"]
    assert "fallback is not possible" in ei.value.message
    assert gem.models_called() == ["g-bal"]


async def test_timeout_is_transient(h: Harness) -> None:
    gem = _gemini()

    async def slow(model: str, key_index: int, req: LLMRequest) -> LLMResponse:
        gem.calls.append((model, key_index, req))
        if model == "g-fast":
            await asyncio.sleep(5)
        return LLMResponse(text="late", provider="gemini", model=model)

    gem.generate = slow  # type: ignore[method-assign]
    gw = h.gateway(gem, settings=h.settings_with(llm_request_timeout_seconds=0.2))
    resp = await gw.generate(_req())
    assert resp.model == "g-bal"
    assert gem.models_called() == ["g-fast"] * 3 + ["g-bal"]


# --- Redis primitives ------------------------------------------------------------------------------------------


def _limits(**limits: int) -> str:
    return json.dumps({"m1": limits, **GENEROUS})


async def test_budget_rpm_window_and_release(h: Harness) -> None:
    settings = h.settings_with(llm_model_limits=_limits(rpm=2, tpm=10_000, rpd=100, concurrency=10))
    budget = ProviderBudget(settings, h.runtime.redis.client, prefix=h.prefix)
    r1, _, _ = await budget.try_reserve("m1", 10)
    r2, _, _ = await budget.try_reserve("m1", 10)
    assert r1 is not None and r2 is not None
    r3, hint, reason = await budget.try_reserve("m1", 10)
    assert r3 is None and reason == "rpm" and 50 < hint <= 60
    # A reservation that never reached the provider is undone completely (rpm, tpm, rpd, slot).
    await budget.release(r2)
    snap = await budget.snapshot("m1")
    assert snap == {"rpm": 1, "tpm": 10, "rpd": 1, "concurrency": 1}
    assert (await budget.try_reserve("m1", 10))[0] is not None
    # P0 gives up at once when the server hint exceeds its remaining wait budget.
    started = time.monotonic()
    assert await budget.reserve("m1", 10, Priority.P0, max_wait_s=2) is None
    assert time.monotonic() - started < 1


async def test_budget_tpm_commit_replaces_estimate_and_frees_slot(h: Harness) -> None:
    settings = h.settings_with(llm_model_limits=_limits(rpm=100, tpm=1_000, rpd=100, concurrency=1))
    budget = ProviderBudget(settings, h.runtime.redis.client, prefix=h.prefix)
    r1, _, _ = await budget.try_reserve("m1", 900)
    assert r1 is not None
    blocked, _, reason = await budget.try_reserve("m1", 200)  # 900 + 200 > 1000
    assert blocked is None and reason == "tpm"
    await budget.commit(r1, 50)  # actual usage was far below the estimate; slot freed
    snap = await budget.snapshot("m1")
    assert snap["tpm"] == 50 and snap["concurrency"] == 0 and snap["rpm"] == 1
    r2, _, _ = await budget.try_reserve("m1", 900)
    assert r2 is not None
    r3, _, reason = await budget.try_reserve("m1", 10)
    assert r3 is None and reason == "concurrency"
    await budget.commit(r2, None)  # keep estimate
    assert (await budget.snapshot("m1"))["tpm"] == 950


async def test_budget_rpd_and_cooldown_extend_only(h: Harness) -> None:
    settings = h.settings_with(llm_model_limits=_limits(rpm=100, tpm=100_000, rpd=1, concurrency=5))
    budget = ProviderBudget(settings, h.runtime.redis.client, prefix=h.prefix)
    assert (await budget.try_reserve("m1", 1))[0] is not None
    res, hint, reason = await budget.try_reserve("m1", 1)
    assert res is None and reason == "rpd" and 0 < hint <= 86_400
    await budget.cooldown("m2", 30)
    await budget.cooldown("m2", 1)  # never shortens
    assert 25 < await budget.cooldown_remaining("m2") <= 30
    res, hint, reason = await budget.try_reserve("m2", 1)
    assert res is None and reason == "cooldown" and 25 < hint <= 30


async def test_budget_reserve_waits_for_a_slot(h: Harness) -> None:
    settings = h.settings_with(llm_model_limits=_limits(rpm=100, tpm=100_000, rpd=100, concurrency=1))
    budget = ProviderBudget(settings, h.runtime.redis.client, prefix=h.prefix, step_seconds=0.05)
    held = await budget.reserve("m1", 1, Priority.P1)
    assert held is not None

    async def free_later() -> None:
        await asyncio.sleep(0.3)
        await budget.commit(held, 1)

    task = asyncio.create_task(free_later())
    started = time.monotonic()
    got = await budget.reserve("m1", 1, Priority.P1, max_wait_s=3)
    await task
    assert got is not None and 0.25 <= time.monotonic() - started < 2.5


async def test_per_key_scope_budgets_each_key_separately(h: Harness) -> None:
    settings = h.settings_with(
        llm_quota_scope="per_key", llm_model_limits=_limits(rpm=1, tpm=100_000, rpd=100, concurrency=5)
    )
    budget = ProviderBudget(settings, h.runtime.redis.client, prefix=h.prefix)
    assert (await budget.try_reserve("m1", 1, key_index=0))[0] is not None
    assert (await budget.try_reserve("m1", 1, key_index=0))[0] is None
    assert (await budget.try_reserve("m1", 1, key_index=1))[0] is not None


async def test_key_pool_lru_rotation_and_cooldown(h: Harness) -> None:
    pool = KeyPool(h.runtime.redis.client, prefix=h.prefix)
    picks = []
    for _ in range(6):
        picks.append(await pool.acquire("p", 3))
        await asyncio.sleep(0.002)  # distinct ms timestamps
    assert picks == [0, 1, 2, 0, 1, 2]
    await pool.record_rate_limited("p", 0, 30)
    await pool.disable("p", 1)
    assert {await pool.acquire("p", 3) for _ in range(3)} == {2}
    await pool.enable("p", 1)
    assert await pool.acquire("p", 3) == 1
    await pool.disable("p", 1)
    await pool.disable("p", 2)
    assert await pool.acquire("p", 3) is None
    assert await pool.acquire("p", 0) is None


async def test_circuit_half_open_admits_exactly_one_probe(h: Harness) -> None:
    cb = CircuitBreaker(h.runtime.redis.client, prefix=h.prefix, threshold=2, open_seconds=0.3)
    assert await cb.allow("p", "m")
    assert await cb.record_failure("p", "m") is CircuitState.CLOSED
    assert await cb.record_failure("p", "m") is CircuitState.OPEN
    assert not await cb.allow("p", "m")
    await asyncio.sleep(0.35)
    assert await cb.allow("p", "m")  # the probe
    assert not await cb.allow("p", "m")  # concurrent callers wait for the probe
    assert await cb.record_failure("p", "m") is CircuitState.OPEN  # failed probe re-opens at once
    assert not await cb.allow("p", "m")
    await asyncio.sleep(0.35)
    assert await cb.allow("p", "m")
    await cb.record_success("p", "m")
    assert await cb.state("p", "m") is CircuitState.CLOSED
    assert await cb.allow("p", "m") and await cb.allow("p", "m")


def _unused() -> Iterator[Callable[..., Any]]:  # keep typing imports honest
    yield from ()
