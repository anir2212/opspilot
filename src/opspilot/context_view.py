"""Small, source-preserving view of investigation evidence for model prompts."""

from __future__ import annotations

from collections.abc import Mapping


def _short(value: object, limit: int) -> str:
    return str(value).replace("\x00", " ")[:limit]


def _recent_values(series: object) -> list[float]:
    values: list[float] = []
    if not isinstance(series, list):
        return values
    for item in series[:3]:
        if not isinstance(item, Mapping):
            continue
        for point in item.get("values", [])[-12:]:
            try:
                values.append(float(point[1]))
            except (ValueError, TypeError, IndexError):
                continue
    return values


def prompt_context(alert: dict, context: dict) -> dict:
    """Keep causal observations and distinct runbooks ahead of bulky raw series.

    The complete tool responses remain in graph state for audit. Only this bounded
    view is sent to an LLM, so a long Prometheus result cannot hide runbooks.
    """
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    service = labels.get("service", "")
    description = " ".join(
        str(value) for value in (annotations.get("description"), annotations.get("summary"))
        if value is not None
    ).lower()
    include_backups = service == "backups" or "backup" in description
    view: dict[str, object] = {}

    for name in ("error_rate", "p95_latency"):
        result = context.get(name, {})
        if not isinstance(result, dict):
            continue
        item: dict[str, object] = {"source_id": result.get("source_id")}
        if "observation" in result:
            item["observation"] = result["observation"]
        values = _recent_values(result.get("series"))
        if values:
            item["recent_samples"] = [round(value, 4) for value in values[-4:]]
            item["recent_min"] = round(min(values), 4)
            item["recent_max"] = round(max(values), 4)
        if result.get("available") is False:
            item["available"] = False
        view[name] = item

    anomaly = context.get("anomalies", {})
    if isinstance(anomaly, dict):
        view["anomalies"] = {
            key: anomaly[key] for key in (
                "source_id", "available", "reason", "baseline_samples",
                "incident_samples", "anomalous_samples", "top_features",
            ) if key in anomaly
        }

    logs = context.get("logs", {})
    if isinstance(logs, dict):
        view["logs"] = {
            "source_id": logs.get("source_id"),
            "available": logs.get("available", True),
            "entries": [_short(row, 250) for row in logs.get("entries", [])[:3]],
        }

    traces = context.get("traces", {})
    if isinstance(traces, dict):
        view["traces"] = {
            "source_id": traces.get("source_id"),
            "available": traces.get("available", True),
            "traces": [
                {
                    "source_id": row.get("source_id"),
                    "root_service": row.get("root_service"),
                    "duration_ms": row.get("duration_ms"),
                    "spans": [
                        {key: span.get(key) for key in ("service", "name", "status", "attributes")}
                        for span in row.get("spans", [])[:5]
                    ],
                }
                for row in traces.get("traces", [])[:2] if isinstance(row, dict)
            ],
        }

    deploys = context.get("deploys", {})
    if isinstance(deploys, dict):
        view["deploys"] = {
            "source_id": deploys.get("source_id"),
            "deploys": deploys.get("deploys", [])[:3],
        }

    runbooks = context.get("runbooks", {})
    if isinstance(runbooks, dict):
        distinct = []
        seen: set[str] = set()
        for row in runbooks.get("results", []):
            if not isinstance(row, dict):
                continue
            source = row.get("source_id")
            if not isinstance(source, str) or source in seen:
                continue
            seen.add(source)
            distinct.append({
                "source_id": source,
                "heading": _short(row.get("heading", ""), 120),
                "body": _short(row.get("body", ""), 900),
            })
            if len(distinct) == 5:
                break
        view["runbooks"] = {"source_id": runbooks.get("source_id"), "results": distinct}

    incidents = context.get("incidents", {})
    if isinstance(incidents, dict):
        view["incidents"] = {
            "source_id": incidents.get("source_id"),
            "results": [
                {"number": row.get("number"), "heading": _short(row.get("heading", ""), 120),
                 "body": _short(row.get("body", ""), 350)}
                for row in incidents.get("results", [])[:2] if isinstance(row, dict)
            ],
        }

    if include_backups:
        backups = context.get("backups", {})
        if isinstance(backups, dict):
            view["backups"] = {
                "source_id": backups.get("source_id"),
                "jobs": backups.get("jobs", [])[:3],
            }
    return view
