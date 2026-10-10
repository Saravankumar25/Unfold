"""LLM Gateway (LLD §13): the single implementation of `LLMClient`.

Per call: redact every text part → response cache (`cacheable` only) → per-job call budget → `ModelRouter`
candidates → per candidate: circuit `allow()` → `ProviderBudget.reserve()` → `KeyPool.acquire()` → provider call
under `llm_request_timeout_seconds` → commit/release + accounting.

Retry policy:
* 429 → cooldown (server `retryDelay`, default 30 s); retry the same model only if the cooldown fits the priority
  wait budget, otherwise next candidate.
* auth → disable that key, try the next key of the same provider.
* transient (5xx / timeout / transport) → retry ×2 with jittered backoff, then circuit failure → next candidate.
* invalid structured output → one repair attempt (validation error fed back), then next candidate.
* bad request → `GenerationError` without fallback, except context-length (only LONG_CONTEXT candidates remain)
  and model-specific errors (next candidate).
Exhausted → `ProviderCapacityError` (retryable).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, GenerationError, ProviderCapacityError, ProviderError, UnfoldError
from unfold.core.logging import get_logger
from unfold.llm._redis import resolve
from unfold.llm.budget import WAIT_BUDGET_S, ProviderBudget
from unfold.llm.cache import ResponseCache
from unfold.llm.circuit import CircuitBreaker
from unfold.llm.key_pool import KeyPool
from unfold.llm.providers.base import (
    AuthError,
    BadRequest,
    InvalidOutput,
    Provider,
    ProviderCallError,
    RateLimited,
    TransientError,
)
from unfold.llm.router import Candidate, ModelRouter
from unfold.llm.schemas import LLMRequest, LLMResponse, Message, Usage
from unfold.observability.tracing import llm_calls, llm_tokens, set_attrs, span
from unfold.persistence.redis_store import RedisStore
from unfold.security.secrets import redact_text

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

log = get_logger(__name__)

DEFAULT_RATE_LIMIT_COOLDOWN_S = 30.0
MAX_TRANSIENT_RETRIES = 2
USAGE_TTL_SECONDS = 7 * 86_400
HARD_STOP_FACTOR = 1.3

Invoke = Callable[[Provider, str, int, LLMRequest], Awaitable[R]]

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


# --- redaction -----------------------------------------------------------------------------------------------
def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def redact_request(req: LLMRequest) -> LLMRequest:
    """Copy of `req` with every outgoing text part passed through `security.secrets.redact`."""
    messages = [
        m.model_copy(
            update={
                "content": redact_text(m.content),
                "tool_calls": [
                    tc.model_copy(update={"arguments": _redact_value(tc.arguments)}) for tc in m.tool_calls
                ],
            }
        )
        for m in req.messages
    ]
    tools = [t.model_copy(update={"description": redact_text(t.description)}) for t in req.tools]
    return req.model_copy(update={"system": redact_text(req.system), "messages": messages, "tools": tools})


def estimate_tokens(req: LLMRequest) -> int:
    """ceil(chars / 4) over every prompt part + max_output_tokens (LLD §13)."""
    chars = len(req.system)
    for m in req.messages:
        chars += len(m.content)
        for tc in m.tool_calls:
            chars += len(tc.name) + len(orjson.dumps(tc.arguments))
    for t in req.tools:
        chars += len(t.name) + len(t.description) + len(orjson.dumps(t.parameters))
    return math.ceil(chars / 4) + req.max_output_tokens


# --- internal control flow -------------------------------------------------------------------------------
class _Skip(Exception):  # control-flow signal, never surfaced to callers
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _CallState:
    retries: int = 0
    rate_limited: int = 0
    key_index: int | None = None
    candidate: Candidate | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)


class LLMGateway:
    """Implements `unfold.llm.schemas.LLMClient`."""

    def __init__(
        self,
        settings: Settings,
        redis: RedisStore,
        providers: Mapping[str, Provider],
        *,
        prefix: str = "llm",
        circuit: CircuitBreaker | None = None,
        budget: ProviderBudget | None = None,
        keys: KeyPool | None = None,
        cache: ResponseCache | None = None,
        backoff_base_s: float = 0.5,
        not_configured_reason: str | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._providers = dict(providers)
        self._prefix = prefix
        self._router = ModelRouter(self._providers)
        self._circuit = circuit or CircuitBreaker(redis.client, prefix=prefix)
        self._budget = budget or ProviderBudget(settings, redis.client, prefix=prefix)
        self._keys = keys or KeyPool(redis.client, prefix=prefix)
        self._cache = cache or ResponseCache(redis, prefix=prefix)
        self._backoff = backoff_base_s
        self._not_configured_reason = not_configured_reason
        self._admit_script = redis.client.register_script(_ADMIT)

    # --- introspection ------------------------------------------------------------------------------------
    @property
    def router(self) -> ModelRouter:
        return self._router

    @property
    def providers(self) -> dict[str, Provider]:
        return dict(self._providers)

    @property
    def configured(self) -> bool:
        return any(p.key_count > 0 for p in self._providers.values())

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise ProviderError(
                self._not_configured_reason
                or "No LLM provider is configured: set GEMINI_API_KEY_1..4 (or GROQ_API_KEY_1..4).",
                code=ErrorCode.PROVIDER_NOT_CONFIGURED,
                retryable=False,
                http_status=503,
            )

    # --- per-job accounting ----------------------------------------------------------------------------------
    def _usage_key(self, job_id: str) -> str:
        return f"usage:{job_id}" if self._prefix == "llm" else f"{self._prefix}:usage:{job_id}"

    async def _admit(self, req: LLMRequest) -> None:
        if not req.job_id:
            return
        soft = max(0, self._settings.analysis_llm_call_budget)
        hard = math.ceil(soft * HARD_STOP_FACTOR)
        tokens = max(0, self._settings.analysis_llm_token_budget)
        result: Any = await self._admit_script(
            keys=[self._usage_key(req.job_id)],
            args=[soft, hard, int(req.priority), USAGE_TTL_SECONDS, tokens],
        )
        if int(result[0]) == 1:
            return
        which = result[1].decode() if isinstance(result[1], bytes) else str(result[1])
        await resolve(self._redis.client.hincrby(self._usage_key(req.job_id), "budget_rejections", 1))
        raise UnfoldError(
            f"LLM call budget exhausted for this analysis ({which} limit; purpose={req.purpose})",
            code=ErrorCode.LLM_BUDGET_EXHAUSTED,
            retryable=False,
            detail={"limit": which, "budget": soft, "hard_limit": hard, "token_budget": tokens},
        )

    async def _record(
        self,
        req: LLMRequest,
        *,
        provider: str,
        model: str,
        usage: Usage,
        st: _CallState,
        outcome: str,
        cached: bool = False,
    ) -> None:
        attrs = {"provider": provider, "model": model, "outcome": outcome, "purpose": req.purpose}
        llm_calls.add(1, attrs)
        if usage.input_tokens:
            llm_tokens.add(usage.input_tokens, {"provider": provider, "model": model, "direction": "input"})
        if usage.output_tokens:
            llm_tokens.add(usage.output_tokens, {"provider": provider, "model": model, "direction": "output"})
        if not req.job_id:
            return
        key = self._usage_key(req.job_id)
        pipe = self._redis.client.pipeline(transaction=False)
        incr = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "retries": st.retries,
            "rate_limited": st.rate_limited,
            f"purpose:{req.purpose}:calls": 1,
            f"purpose:{req.purpose}:input_tokens": usage.input_tokens,
            f"purpose:{req.purpose}:output_tokens": usage.output_tokens,
            f"outcome:{outcome}": 1,
        }
        if cached:
            incr["cache_hits"] = 1
        else:
            incr[f"model:{model}:calls"] = 1
            incr[f"model:{model}:input_tokens"] = usage.input_tokens
            incr[f"model:{model}:output_tokens"] = usage.output_tokens
        for name, amount in incr.items():
            if amount:
                pipe.hincrby(key, name, int(amount))
        pipe.expire(key, USAGE_TTL_SECONDS)
        await pipe.execute()

    async def usage(self, job_id: str) -> dict[str, Any]:
        """Per-job accounting: totals plus `by_purpose` / `by_model` breakdowns."""
        raw = await resolve(self._redis.client.hgetall(self._usage_key(job_id)))
        flat = {(k.decode() if isinstance(k, bytes) else str(k)): int(v) for k, v in raw.items()}
        out: dict[str, Any] = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "retries": 0,
            "rate_limited": 0,
            "cache_hits": 0,
            "budget_rejections": 0,
            "outcomes": {},
            "by_purpose": {},
            "by_model": {},
        }
        for name, value in flat.items():
            if name.startswith("purpose:"):
                purpose, metric = name[len("purpose:") :].rsplit(":", 1)
                out["by_purpose"].setdefault(purpose, {})[metric] = value
            elif name.startswith("model:"):
                model, metric = name[len("model:") :].rsplit(":", 1)
                out["by_model"].setdefault(model, {})[metric] = value
            elif name.startswith("outcome:"):
                out["outcomes"][name[len("outcome:") :]] = value
            else:
                out[name] = value
        return out

    # --- candidate execution ---------------------------------------------------------------------------------
    async def _run_on_candidate(
        self,
        cand: Candidate,
        req: LLMRequest,
        invoke: Invoke[R],
        usage_of: Callable[[R], Usage | None],
        st: _CallState,
        deadline: float,
    ) -> R:
        provider = self._providers[cand.provider]
        per_key = self._settings.llm_quota_scope == "per_key"
        est = estimate_tokens(req)
        timeout = self._settings.llm_request_timeout_seconds
        transient = 0
        if not await self._circuit.allow(cand.provider, cand.model):
            raise _Skip("circuit_open")
        while True:
            key_idx: int | None = None
            if per_key:
                key_idx = await self._keys.acquire(cand.provider, provider.key_count)
                if key_idx is None:
                    raise _Skip("no_healthy_key")
            reservation = await self._budget.reserve(
                cand.model, est, req.priority, key_index=key_idx, max_wait_s=deadline - time.monotonic()
            )
            if reservation is None:
                raise _Skip("budget_exhausted")
            if key_idx is None:
                key_idx = await self._keys.acquire(cand.provider, provider.key_count)
                if key_idx is None:
                    await self._budget.release(reservation)
                    raise _Skip("no_healthy_key")
            st.key_index = key_idx
            try:
                async with asyncio.timeout(timeout):
                    result = await invoke(provider, cand.model, key_idx, req)
            except RateLimited as exc:
                await self._budget.commit(reservation, 0)
                cooldown = (
                    exc.retry_after_s if exc.retry_after_s is not None else DEFAULT_RATE_LIMIT_COOLDOWN_S
                )
                cooldown = max(0.0, cooldown)
                st.rate_limited += 1
                if per_key:
                    await self._keys.record_rate_limited(cand.provider, key_idx, cooldown)
                    await self._budget.cooldown(cand.model, cooldown, key_index=key_idx)
                else:
                    await self._keys.record_rate_limited(cand.provider, key_idx)
                    await self._budget.cooldown(cand.model, cooldown)
                log.warning(
                    "llm.rate_limited",
                    provider=cand.provider,
                    model=cand.model,
                    key_index=key_idx,
                    cooldown_s=cooldown,
                    purpose=req.purpose,
                )
                if time.monotonic() + cooldown <= deadline:
                    st.retries += 1
                    continue
                raise _Skip("rate_limited") from exc
            except AuthError as exc:
                await self._budget.release(reservation)
                await self._keys.disable(cand.provider, key_idx)
                log.error("llm.key_disabled", provider=cand.provider, key_index=key_idx, reason=exc.message)
                st.retries += 1
                continue
            except (TransientError, TimeoutError) as exc:
                await self._budget.commit(reservation, 0)
                await self._keys.record_failure(cand.provider, key_idx)
                transient += 1
                log.warning(
                    "llm.transient_error",
                    provider=cand.provider,
                    model=cand.model,
                    key_index=key_idx,
                    attempt=transient,
                    error=type(exc).__name__,
                    purpose=req.purpose,
                )
                if transient <= MAX_TRANSIENT_RETRIES:
                    st.retries += 1
                    delay = self._backoff * (2 ** (transient - 1)) * (0.5 + random.random())
                    await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
                    continue
                await self._circuit.record_failure(cand.provider, cand.model)
                raise _Skip("transient") from exc
            except InvalidOutput:
                await self._budget.commit(reservation, None)
                await self._circuit.record_success(cand.provider, cand.model)
                await self._keys.record_success(cand.provider, key_idx)
                raise
            except BadRequest:
                await self._budget.commit(reservation, 0)
                await self._circuit.record_success(cand.provider, cand.model)
                raise
            except ProviderCallError as exc:  # unknown subclass: treat as transient without retry
                await self._budget.commit(reservation, 0)
                await self._circuit.record_failure(cand.provider, cand.model)
                raise _Skip(f"provider_error:{type(exc).__name__}") from exc
            except BaseException:
                await self._budget.release(reservation)
                raise
            usage = usage_of(result)
            await self._budget.commit(
                reservation, None if usage is None else usage.input_tokens + usage.output_tokens
            )
            await self._circuit.record_success(cand.provider, cand.model)
            await self._keys.record_success(cand.provider, key_idx)
            st.candidate = cand
            return result

    async def _with_fallback(
        self,
        req: LLMRequest,
        invoke: Invoke[R],
        usage_of: Callable[[R], Usage | None],
        st: _CallState,
        *,
        repair: Callable[[LLMRequest, InvalidOutput], LLMRequest] | None = None,
    ) -> tuple[Candidate, R]:
        candidates = self._router.candidates(req.capability, req.exclude_models)
        if not candidates:
            raise ProviderCapacityError(
                f"No model candidate available for capability {req.capability.value}",
                detail={"capability": req.capability.value, "excluded": sorted(req.exclude_models)},
            )
        deadline = time.monotonic() + WAIT_BUDGET_S[req.priority]
        allowed: set[Candidate] | None = None
        for cand in candidates:
            if allowed is not None and cand not in allowed:
                continue
            attempt_req = req
            repaired = False
            while True:
                try:
                    result = await self._run_on_candidate(cand, attempt_req, invoke, usage_of, st, deadline)
                    return cand, result
                except _Skip as skip:
                    st.failures.append((cand.label, skip.reason))
                    break
                except InvalidOutput as exc:
                    st.failures.append((cand.label, "invalid_output"))
                    if repair is not None and not repaired:
                        repaired = True
                        st.retries += 1
                        attempt_req = repair(req, exc)
                        continue
                    break
                except BadRequest as exc:
                    if exc.model_specific:
                        st.failures.append((cand.label, "model_unavailable"))
                        break
                    if exc.context_length:
                        st.failures.append((cand.label, "context_length"))
                        allowed = {c for c in self._router.long_context() if c != cand}
                        if allowed & set(candidates[candidates.index(cand) + 1 :]):
                            break
                    raise GenerationError(
                        f"Model rejected the request ({cand.label}): {exc.message}",
                        code=ErrorCode.GENERATION_FAILED,
                        retryable=False,
                        detail={"provider": cand.provider, "model": cand.model},
                    ) from exc
        reasons = {reason for _, reason in st.failures}
        detail = {"attempts": [{"candidate": c, "reason": r} for c, r in st.failures]}
        if reasons and reasons <= {"invalid_output"}:
            raise GenerationError(
                "Every model candidate returned invalid output",
                code=ErrorCode.INVALID_MODEL_OUTPUT,
                retryable=True,
                detail=detail,
            )
        raise ProviderCapacityError(
            "LLM capacity exhausted for this request; retry later", retryable=True, detail=detail
        )

    # --- LLMClient ------------------------------------------------------------------------------------------
    async def generate(self, req: LLMRequest) -> LLMResponse:
        self._ensure_configured()
        red = redact_request(req)
        started = time.perf_counter()
        st = _CallState()
        with span(
            "llm.generate", purpose=req.purpose, capability=req.capability.value, priority=int(req.priority)
        ) as s:
            if red.cacheable:
                hit = await self._cache.get(red, "text")
                if hit is not None:
                    resp = LLMResponse.model_validate(hit).model_copy(
                        update={"cached": True, "latency_ms": (time.perf_counter() - started) * 1000}
                    )
                    await self._record(
                        red,
                        provider=resp.provider,
                        model=resp.model,
                        usage=Usage(),
                        st=st,
                        outcome="cache_hit",
                        cached=True,
                    )
                    set_attrs(s, provider=resp.provider, model=resp.model, outcome="cache_hit", cached=True)
                    return resp
            await self._admit(red)
            try:
                cand, resp = await self._with_fallback(
                    red, lambda p, m, k, r: p.generate(m, k, r), lambda r: r.usage, st
                )
            except UnfoldError as exc:
                await self._fail(red, st, s, started, exc)
                raise
            resp = resp.model_copy(update={"latency_ms": (time.perf_counter() - started) * 1000})
            await self._succeed(red, st, s, cand, resp.usage, resp.latency_ms)
            if red.cacheable and not resp.tool_calls:
                await self._cache.set(red, resp.model_dump(mode="json"), "text")
            return resp

    async def generate_structured(self, req: LLMRequest, schema: type[T]) -> T:
        self._ensure_configured()
        red = redact_request(req)
        started = time.perf_counter()
        st = _CallState()
        json_schema = schema.model_json_schema()
        kind = f"json:{schema.__qualname__}:{_schema_digest(json_schema)}"
        with span(
            "llm.generate",
            purpose=req.purpose,
            capability=req.capability.value,
            priority=int(req.priority),
            structured=schema.__name__,
        ) as s:
            if red.cacheable:
                hit = await self._cache.get(red, kind)
                if hit is not None:
                    try:
                        value = schema.model_validate(hit)
                    except ValidationError:
                        value = None
                    if value is not None:
                        await self._record(
                            red,
                            provider="cache",
                            model="cache",
                            usage=Usage(),
                            st=st,
                            outcome="cache_hit",
                            cached=True,
                        )
                        set_attrs(s, outcome="cache_hit", cached=True)
                        return value
            await self._admit(red)

            async def invoke(p: Provider, m: str, k: int, r: LLMRequest) -> tuple[T, LLMResponse]:
                data, resp = await p.generate_json(m, k, r, json_schema)
                try:
                    return schema.model_validate(data), resp
                except ValidationError as exc:
                    raise InvalidOutput(_validation_summary(exc), raw=resp.text[:8000]) from exc

            try:
                cand, (value, resp) = await self._with_fallback(
                    red, invoke, lambda r: r[1].usage, st, repair=_repair_request
                )
            except UnfoldError as exc:
                await self._fail(red, st, s, started, exc)
                raise
            await self._succeed(red, st, s, cand, resp.usage, (time.perf_counter() - started) * 1000)
            if red.cacheable:
                await self._cache.set(red, value.model_dump(mode="json"), kind)
            return value

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        """Text deltas. Fallback to another candidate is only possible before the first token is emitted."""
        self._ensure_configured()
        red = redact_request(req)
        started = time.perf_counter()
        st = _CallState()
        timeout = self._settings.llm_request_timeout_seconds

        async def invoke(p: Provider, m: str, k: int, r: LLMRequest) -> tuple[str | None, AsyncIterator[str]]:
            agen = p.stream(m, k, r)
            try:
                first: str | None = await anext(agen)
            except StopAsyncIteration:
                first = None
            except BaseException:
                await _aclose(agen)
                raise
            return first, agen

        with span(
            "llm.generate",
            purpose=req.purpose,
            capability=req.capability.value,
            priority=int(req.priority),
            streaming=True,
        ) as s:
            await self._admit(red)
            try:
                cand, (first, agen) = await self._with_fallback(red, invoke, lambda _r: None, st)
            except UnfoldError as exc:
                await self._fail(red, st, s, started, exc)
                raise
            set_attrs(
                s,
                provider=cand.provider,
                model=cand.model,
                key_index=st.key_index,
                retries=st.retries,
                ttft_ms=(time.perf_counter() - started) * 1000,
            )
        chars = 0
        outcome = "ok"
        try:
            if first is not None:
                chars += len(first)
                yield first
            while True:
                try:
                    async with asyncio.timeout(timeout):
                        chunk = await anext(agen)
                except StopAsyncIteration:
                    break
                chars += len(chunk)
                yield chunk
        except (ProviderCallError, TimeoutError) as exc:
            outcome = "interrupted"
            raise ProviderError(
                f"Stream interrupted after the first token ({cand.label}); fallback is not possible",
                detail={"provider": cand.provider, "model": cand.model},
            ) from exc
        finally:
            await _aclose(agen)
            usage = Usage(
                input_tokens=estimate_tokens(red) - red.max_output_tokens, output_tokens=math.ceil(chars / 4)
            )
            await self._record(
                red, provider=cand.provider, model=cand.model, usage=usage, st=st, outcome=outcome
            )
            log.info(
                "llm.stream",
                purpose=red.purpose,
                provider=cand.provider,
                model=cand.model,
                key_index=st.key_index,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                output_chars=chars,
                retries=st.retries,
                outcome=outcome,
            )

    # --- outcome helpers ---------------------------------------------------------------------------------------
    async def _succeed(
        self, req: LLMRequest, st: _CallState, s: Any, cand: Candidate, usage: Usage, latency_ms: float
    ) -> None:
        set_attrs(
            s,
            provider=cand.provider,
            model=cand.model,
            key_index=st.key_index,
            latency_ms=round(latency_ms, 1),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            retries=st.retries,
            rate_limited=st.rate_limited,
            outcome="ok",
        )
        await self._record(req, provider=cand.provider, model=cand.model, usage=usage, st=st, outcome="ok")
        log.info(
            "llm.call",
            purpose=req.purpose,
            provider=cand.provider,
            model=cand.model,
            key_index=st.key_index,
            latency_ms=round(latency_ms, 1),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            retries=st.retries,
            outcome="ok",
        )

    async def _fail(self, req: LLMRequest, st: _CallState, s: Any, started: float, exc: UnfoldError) -> None:
        latency_ms = (time.perf_counter() - started) * 1000
        set_attrs(
            s,
            outcome=exc.code.value,
            retries=st.retries,
            rate_limited=st.rate_limited,
            latency_ms=round(latency_ms, 1),
            attempts=len(st.failures),
        )
        await self._record(req, provider="none", model="none", usage=Usage(), st=st, outcome=exc.code.value)
        log.warning(
            "llm.call_failed",
            purpose=req.purpose,
            code=exc.code.value,
            retries=st.retries,
            attempts=[f"{c}={r}" for c, r in st.failures],
            latency_ms=round(latency_ms, 1),
        )


def _schema_digest(json_schema: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(json_schema, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]


def _validation_summary(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:10]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "schema validation failed: " + "; ".join(parts)


def _repair_request(req: LLMRequest, exc: InvalidOutput) -> LLMRequest:
    """Feed the invalid output + error back once (the repair attempt)."""
    extra: list[Message] = []
    if exc.raw:
        extra.append(Message(role="assistant", content=redact_text(exc.raw)))
    extra.append(
        Message(
            role="user",
            content=(
                f"Your previous reply was rejected: {redact_text(exc.message)[:2000]}\n"
                "Reply again with ONLY a single JSON object that satisfies the required schema. "
                "Do not include explanations or code fences."
            ),
        )
    )
    return req.model_copy(update={"messages": [*req.messages, *extra], "cacheable": False})


async def _aclose(agen: AsyncIterator[str]) -> None:
    close = getattr(agen, "aclose", None)
    if close is not None:
        with contextlib.suppress(Exception):  # best-effort cleanup of a provider stream
            await close()


__all__ = ["LLMGateway", "estimate_tokens", "redact_request"]
