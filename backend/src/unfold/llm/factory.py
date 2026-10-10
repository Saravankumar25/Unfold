"""Gateway composition (LLD §13). Called once by a composition root (api / worker / cli).

Without any provider keys the gateway is still built (so the API starts and serves non-LLM routes); every LLM
call then raises `ProviderError(code=PROVIDER_NOT_CONFIGURED)`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unfold.core.config import Settings
from unfold.llm.gateway import LLMGateway
from unfold.llm.providers.base import Provider
from unfold.ports import CounterStore

if TYPE_CHECKING:
    from unfold.persistence.redis_store import RedisStore


def configured_providers(settings: Settings) -> list[str]:
    """Provider names that have at least one key, in routing order (Gemini first — HLD D1)."""
    out: list[str] = []
    if settings.gemini_keys:
        out.append("gemini")
    if settings.groq_keys:
        out.append("groq")
    return out


def build_providers(settings: Settings) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    if settings.gemini_keys:
        from unfold.llm.providers.gemini import GeminiProvider

        providers["gemini"] = GeminiProvider(settings, settings.gemini_keys)
    if settings.groq_keys:
        from unfold.llm.providers.groq import GroqProvider

        providers["groq"] = GroqProvider(settings, settings.groq_keys)
    return providers


def build_gateway(
    settings: Settings, redis: RedisStore | None, *, counters: CounterStore | None = None
) -> LLMGateway:
    """Redis-backed coordination (local stack), or the counter-store one when `redis` is None (AWS profile)."""
    providers = build_providers(settings)
    reason = None
    if not providers:
        reason = (
            "No LLM provider is configured. Set GEMINI_API_KEY_1..4 (and optionally GROQ_API_KEY_1..4) "
            "in the environment, then restart the api/worker."
        )
    if redis is not None:
        return LLMGateway(settings, redis, providers, not_configured_reason=reason)
    if counters is None:
        raise ValueError("build_gateway needs Redis or a counter store")
    from unfold.llm.counter_coordination import (
        CounterKeyPool,
        CounterProviderBudget,
        CounterUsageStore,
        LocalCircuitBreaker,
        NoCache,
    )

    return LLMGateway(
        settings,
        None,
        providers,
        circuit=LocalCircuitBreaker(),
        budget=CounterProviderBudget(settings, counters),
        keys=CounterKeyPool(counters),
        cache=NoCache(),
        usage=CounterUsageStore(counters),
        not_configured_reason=reason,
    )
