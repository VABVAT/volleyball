"""OpenTelemetry tracer setup; no-op when OTLP_ENDPOINT is unset."""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def init_tracer(service_name: str) -> trace.Tracer:
    endpoint = os.getenv("OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return trace.get_tracer(service_name)

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        return trace.get_tracer(service_name)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def headers_from_kafka(msg_headers: list[tuple[str, bytes]] | None) -> dict[str, str]:
    if not msg_headers:
        return {}
    return {k: (v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)) for k, v in msg_headers}


def kafka_headers_from_carrier(carrier: dict[str, str]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for k, v in carrier.items():
        out.append((k, v.encode("utf-8")))
    return out
