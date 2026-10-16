"""Infer service dependency edges from the collector's exported OTLP spans."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path


def _service(resource: dict) -> str | None:
    for attribute in resource.get("attributes", []):
        if attribute.get("key") == "service.name":
            return attribute.get("value", {}).get("stringValue")
    return None


def dependencies_from_file(path: str | Path, *, max_batches: int = 500) -> dict[str, set[str]]:
    """Return caller→callee edges only when a cross-service parent span is observed."""
    source = Path(path)
    if not source.is_file():
        return {}
    with source.open() as stream:
        batches = deque(stream, maxlen=max_batches)
    spans = []
    for line in batches:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for resource_spans in payload.get("resourceSpans", []):
            service = _service(resource_spans.get("resource", {}))
            if not service:
                continue
            for scope in resource_spans.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    spans.append((service, span))
    by_id = {
        (span.get("traceId"), span.get("spanId")): service
        for service, span in spans if span.get("traceId") and span.get("spanId")
    }
    dependencies: dict[str, set[str]] = {}
    for child_service, span in spans:
        parent_service = by_id.get((span.get("traceId"), span.get("parentSpanId")))
        if parent_service and parent_service != child_service:
            dependencies.setdefault(parent_service, set()).add(child_service)
    return dependencies
