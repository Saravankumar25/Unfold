"""Provider protocol + typed provider exceptions (LLD §13).

Providers translate `LLMRequest` ⇄ one vendor API for one (model, key index). They never retry, never sleep and
never touch Redis: retry/fallback/budget policy lives in `unfold.llm.gateway`. Every vendor failure is mapped to
exactly one of the exceptions below so the gateway can apply the policy uniformly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

import orjson

from unfold.domain.enums import ModelCapability
from unfold.llm.schemas import LLMRequest, LLMResponse

# Capability → ordered model tiers (LLD §13). Providers map tiers to their concrete models.
CAPABILITY_TIERS: dict[ModelCapability, tuple[str, ...]] = {
    ModelCapability.REASONING: ("reasoning", "balanced"),
    ModelCapability.BALANCED: ("balanced", "reasoning", "fast"),
    ModelCapability.FAST: ("fast", "balanced"),
    ModelCapability.LONG_CONTEXT: ("balanced", "reasoning"),
    ModelCapability.STRUCTURED: ("balanced", "fast"),
    ModelCapability.EVALUATOR: ("balanced", "fast"),
}


class ProviderCallError(Exception):
    """Base for all provider failures. Messages never contain prompt text or key material."""

    retryable: bool = False

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class RateLimited(ProviderCallError):
    """429 / RESOURCE_EXHAUSTED. `retry_after_s` is the server hint (RetryInfo.retryDelay / Retry-After)."""

    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None, status: int | None = 429) -> None:
        super().__init__(message, status=status)
        self.retry_after_s = retry_after_s


class AuthError(ProviderCallError):
    """Credential rejected (401/403/PERMISSION_DENIED/API key invalid). The key is disabled by the gateway."""


class TransientError(ProviderCallError):
    """5xx, timeout, connection failure: bounded retry, then circuit failure and fallback."""

    retryable = True


class InvalidOutput(ProviderCallError):
    """The model answered but the output is unusable (malformed JSON / function call)."""

    def __init__(self, message: str, *, raw: str = "", status: int | None = None) -> None:
        super().__init__(message, status=status)
        self.raw = raw


class BadRequest(ProviderCallError):
    """Non-retryable request problem.

    `context_length`: the prompt exceeds the model's window (a LONG_CONTEXT candidate may still succeed).
    `model_specific`: this model cannot serve the request (e.g. 404 unknown model) — another model may.
    """

    def __init__(
        self,
        message: str,
        *,
        context_length: bool = False,
        model_specific: bool = False,
        status: int | None = 400,
    ) -> None:
        super().__init__(message, status=status)
        self.context_length = context_length
        self.model_specific = model_specific


@runtime_checkable
class Provider(Protocol):
    """One vendor. `key_index` selects the credential (0-based); keys themselves never leave the provider."""

    name: str

    @property
    def key_count(self) -> int: ...

    def models_for(self, capability: ModelCapability) -> list[str]: ...

    async def generate(self, model: str, key_index: int, req: LLMRequest) -> LLMResponse: ...

    def stream(self, model: str, key_index: int, req: LLMRequest) -> AsyncIterator[str]: ...

    async def generate_json(
        self, model: str, key_index: int, req: LLMRequest, json_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], LLMResponse]: ...


def tier_models(tiers: dict[str, str | None], capability: ModelCapability) -> list[str]:
    """Resolve `CAPABILITY_TIERS[capability]` through a provider's tier→model map (deduplicated, ordered)."""
    out: list[str] = []
    for tier in CAPABILITY_TIERS[capability]:
        model = tiers.get(tier)
        if model and model not in out:
            out.append(model)
    return out


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text (tolerates ```json fences). Raises InvalidOutput."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        value = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise InvalidOutput(f"output is not valid JSON: {exc}", raw=text[:4000]) from exc
    if not isinstance(value, dict):
        raise InvalidOutput("output JSON is not an object", raw=text[:4000])
    return value
