"""Time-window incident grouping and transparent service-cause ranking."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from opspilot.trace_graph import dependencies_from_file


@dataclass(frozen=True)
class EvidenceEvent:
    event_id: str
    service: str
    kind: str
    occurred_at: datetime
    source_id: str


SERVICE_DEPENDENCIES = {"gateway": {"payments"}, "payments": set(), "backups": set()}


def correlate(
    anchor: EvidenceEvent,
    events: list[EvidenceEvent],
    *,
    window_minutes: int = 10,
    dependencies: dict[str, set[str]] | None = None,
) -> dict:
    """Rank earliest anomalous services; downstream effects add supporting evidence."""
    window = timedelta(minutes=window_minutes)
    related = [event for event in events if abs(event.occurred_at - anchor.occurred_at) <= window]
    if dependencies is None:
        trace_file = os.getenv("OPSPILOT_TRACE_FILE", "")
        observed = dependencies_from_file(trace_file) if trace_file else {}
        dependencies = observed or SERVICE_DEPENDENCIES
        graph_source = "observed-traces" if observed else "static-fallback"
    else:
        graph_source = "provided"
    grouped: dict[str, list[EvidenceEvent]] = {}
    for event in related:
        grouped.setdefault(event.service, []).append(event)
    ranked: list[dict] = []
    for service, service_events in grouped.items():
        anomaly_times = [e.occurred_at for e in service_events if e.kind == "anomaly"]
        first = min(anomaly_times or [e.occurred_at for e in service_events])
        evidence = len(service_events)
        downstream = sum(
            1 for other in grouped if service in dependencies.get(other, set())
        )
        ranked.append(
            {
                "service": service,
                "first_onset": first.isoformat(),
                "evidence_count": evidence,
                "downstream_services": downstream,
                "source_ids": sorted({event.source_id for event in service_events}),
            }
        )
    ranked.sort(
        key=lambda row: (row["first_onset"], -row["downstream_services"], -row["evidence_count"])
    )
    return {
        "anchor": anchor.event_id,
        "dependency_graph_source": graph_source,
        "services": ranked,
        "event_ids": [e.event_id for e in related],
    }
