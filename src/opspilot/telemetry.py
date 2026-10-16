"""Opt-in OTLP traces/logs plus Prometheus self-observability counters."""

from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Histogram

AGENT_RUNS = Counter(
    "opspilot_agent_runs_total", "Completed agent runs", ["outcome"]
)
AGENT_DURATION = Histogram(
    "opspilot_agent_duration_seconds", "End-to-end agent run duration"
)
TOOL_CALLS = Counter(
    "opspilot_tool_calls_total", "Attempted agent tool calls", ["tool", "outcome"]
)
TOOL_DURATION = Histogram(
    "opspilot_tool_duration_seconds", "Agent tool call duration", ["tool"]
)

_configured = False


def configure_telemetry(app, service: str) -> None:
    """Enable OTel only in the demo containers; tests remain in-process and quiet."""
    global _configured
    if os.getenv("OPSPILOT_OTEL_ENABLED") != "1" or _configured:
        return
    from opentelemetry import _logs, trace
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = os.getenv("OPSPILOT_OTLP_ENDPOINT", "http://otel-collector:4318").rstrip("/")
    resource = Resource.create({"service.name": service, "service.namespace": "opspilot"})
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    ))
    trace.set_tracer_provider(traces)
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")
    ))
    _logs.set_logger_provider(logs)
    logging.getLogger("opspilot").addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=logs)
    )
    FastAPIInstrumentor.instrument_app(app, tracer_provider=traces)
    HTTPXClientInstrumentor().instrument(tracer_provider=traces)
    _configured = True
