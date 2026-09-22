"""structlog configuration (LLD §22). Context (request/project/job/trace ids) flows via contextvars."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from unfold.core.config import Settings
from unfold.security.secrets import REDACTED, redact_text

_SENSITIVE_KEYS = ("api_key", "apikey", "token", "secret", "password", "authorization", "cookie")


def _redact_processor(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in list(event.items()):
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            event[key] = REDACTED
        elif isinstance(value, str) and len(value) > 12:
            event[key] = redact_text(value)
    return event


def _otel_ids(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            event.setdefault("trace_id", format(ctx.trace_id, "032x"))
            event.setdefault("span_id", format(ctx.span_id, "016x"))
    except Exception:  # noqa: S110 - logging must never fail
        pass
    return event


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _otel_ids,
        _redact_processor,
    ]
    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging (uvicorn, arq, neo4j, …) through the same pipeline.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processor=renderer, foreign_pre_chain=shared))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "neo4j", "urllib3", "asyncio", "google_genai", "fastembed"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)


bind_context = structlog.contextvars.bind_contextvars
clear_context = structlog.contextvars.clear_contextvars
