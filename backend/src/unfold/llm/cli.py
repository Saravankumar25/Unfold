"""Operator CLI (LLD §13): `python -m unfold.llm.cli models|ping`.

`models` prints configured providers, capability → candidate mapping and quota limits (never key material).
`ping` performs one tiny FAST/P0 call through the full gateway (budget, circuit, key pool, accounting).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import orjson

from unfold.core.config import Settings, get_settings
from unfold.core.errors import UnfoldError
from unfold.domain.enums import ModelCapability, Priority
from unfold.llm.factory import build_gateway, configured_providers
from unfold.llm.schemas import LLMRequest, Message
from unfold.persistence.redis_store import RedisStore


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def describe(settings: Settings) -> dict[str, object]:
    """Configuration summary without secrets (also used by tests)."""
    from unfold.llm.factory import build_providers
    from unfold.llm.router import ModelRouter

    providers = build_providers(settings)
    router = ModelRouter(providers)
    models = sorted({c.model for cap in ModelCapability for c in router.candidates(cap)})
    return {
        "providers": {name: {"keys": p.key_count} for name, p in providers.items()},
        "quota_scope": settings.llm_quota_scope,
        "analysis_llm_call_budget": settings.analysis_llm_call_budget,
        "tiers": {
            "gemini": {
                "fast": settings.gemini_model_fast,
                "balanced": settings.gemini_model_balanced,
                "reasoning": settings.gemini_model_reasoning,
            },
            "groq": {"fast": settings.groq_model_fast, "balanced": settings.groq_model_balanced},
        },
        "capabilities": {cap.value: [c.label for c in router.candidates(cap)] for cap in ModelCapability},
        "limits": {m: settings.model_limits(m) for m in models},
    }


def cmd_models(settings: Settings) -> int:
    info = describe(settings)
    if not configured_providers(settings):
        _out("WARNING: no LLM provider configured (set GEMINI_API_KEY_1..4).")
    _out(orjson.dumps(info, option=orjson.OPT_INDENT_2).decode())
    return 0


async def _ping(settings: Settings) -> int:
    if not configured_providers(settings):
        _out("ERROR: no LLM provider configured. Set GEMINI_API_KEY_1..4 (optionally GROQ_API_KEY_1..4).")
        return 2
    redis = RedisStore(settings)
    try:
        gateway = build_gateway(settings, redis)
        req = LLMRequest(
            purpose="cli.ping",
            capability=ModelCapability.FAST,
            priority=Priority.P0,
            system="You are a health check. Reply with exactly one word.",
            messages=[Message(role="user", content="Reply with the single word: pong")],
            temperature=0.0,
            max_output_tokens=16,
        )
        try:
            resp = await gateway.generate(req)
        except UnfoldError as exc:
            _out(f"ERROR {exc.code.value}: {exc.message}")
            if exc.detail:
                _out(orjson.dumps(exc.detail).decode())
            return 1
        _out(
            f"OK provider={resp.provider} model={resp.model} latency_ms={resp.latency_ms:.0f} "
            f"tokens_in={resp.usage.input_tokens} tokens_out={resp.usage.output_tokens} text={resp.text.strip()!r}"
        )
        return 0
    finally:
        await redis.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m unfold.llm.cli", description="Unfold LLM gateway tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("models", help="show providers, tier mapping and limits (no secrets)")
    sub.add_parser("ping", help="one tiny FAST call through the gateway")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "models":
        return cmd_models(settings)
    return asyncio.run(_ping(settings))


if __name__ == "__main__":
    raise SystemExit(main())
