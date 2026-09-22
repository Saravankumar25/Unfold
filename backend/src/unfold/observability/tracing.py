"""OpenTelemetry setup + helpers (LLD §22). Spans are always created; exported only if an OTLP endpoint is set."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from unfold.core.config import Settings

_configured = False


def configure_tracing(settings: Settings, component: str) -> None:
    global _configured
    if _configured:
        return
    resource = Resource.create(
        {
            "service.name": f"{settings.otel_service_name}-{component}",
            "deployment.environment": settings.app_env,
        }
    )
    provider = TracerProvider(resource=resource)
    readers: list[Any] = []
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        endpoint = settings.otel_exporter_otlp_endpoint
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))
    _configured = True


tracer = trace.get_tracer("unfold")
meter = metrics.get_meter("unfold")

stage_duration = meter.create_histogram(
    "unfold.stage.duration", unit="s", description="Analysis stage duration"
)
llm_calls = meter.create_counter("unfold.llm.calls", description="LLM calls by provider/model/outcome")
llm_tokens = meter.create_counter("unfold.llm.tokens", description="LLM tokens by provider/model/direction")
retrieval_latency = meter.create_histogram("unfold.retrieval.latency", unit="s")
tool_calls = meter.create_counter("unfold.agent.tool_calls", description="Agent tool calls by agent/tool")


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, str | bool | int | float) else str(v)
    return out


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    with tracer.start_as_current_span(name, attributes=_clean(attrs)) as s:
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def set_attrs(s: Span, **attrs: Any) -> None:
    s.set_attributes(_clean(attrs))
