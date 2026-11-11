"""Apply the evaluated detectors to bounded, pre-incident Prometheus samples."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from opspilot.anomaly import MultivariateIsolationForest, Observation, STLMedianAbsoluteDeviation


def _values(series: list[dict]) -> dict[int, float]:
    if not series:
        return {}
    values = {}
    for timestamp, raw in series[0].get("values", []):
        value = float(raw)
        if math.isfinite(value):
            values[int(round(float(timestamp) / 5)) * 5] = value
    return values


def score_live_series(args, series: dict[str, list[dict]]) -> dict:
    source = f"anomaly:{args.service}"
    if args.incident_start is None or args.incident_start.tzinfo is None:
        return {
            "source_id": source, "available": False,
            "reason": "Incident start time is needed to separate baseline from fault data",
        }
    values = {name: _values(rows) for name, rows in series.items()}
    timestamps = sorted(set.intersection(*(set(rows) for rows in values.values())))
    observations = [
        Observation(
            timestamp=datetime.fromtimestamp(timestamp, UTC).isoformat(),
            service=args.service,
            latency_seconds=values["p95_latency"][timestamp],
            latency_p50_seconds=values["p50_latency"][timestamp],
            error_rate=values["error_rate"][timestamp],
            cpu_ratio=values["cpu_usage"][timestamp],
            memory_ratio=values["memory_bytes"][timestamp] / 1024**3,
        )
        for timestamp in timestamps
    ]
    incident_timestamp = args.incident_start.timestamp()
    baseline = [row for row in observations if datetime.fromisoformat(row.timestamp).timestamp()
                < incident_timestamp][-60:]
    observed = [row for row in observations if datetime.fromisoformat(row.timestamp).timestamp()
                >= incident_timestamp][:24]
    if len(baseline) < 20 or not observed:
        return {
            "source_id": source, "available": False, "baseline_samples": len(baseline),
            "incident_samples": len(observed),
            "reason": "Need at least 20 complete pre-incident samples and one incident sample",
        }
    latency = STLMedianAbsoluteDeviation().fit([row.latency_seconds for row in baseline])
    errors = STLMedianAbsoluteDeviation().fit([row.error_rate for row in baseline])
    forest = MultivariateIsolationForest().fit(baseline)
    combined = baseline + observed
    offset = len(baseline)
    flags = [
        a or b or c
        for a, b, c in zip(
            latency.flags([row.latency_seconds for row in combined])[offset:],
            errors.flags([row.error_rate for row in combined])[offset:],
            forest.flags(observed), strict=True,
        )
    ]
    first = next((row.timestamp for row, flagged in zip(observed, flags, strict=True)
                  if flagged), None)
    latest = observed[-1]
    return {
        "source_id": source, "available": True,
        "method": "STL/MAD plus healthy-trained IsolationForest",
        "baseline": "Prometheus samples before the alert start; fault gauge excluded",
        "baseline_samples": len(baseline), "incident_samples": len(observed),
        "anomalous_samples": sum(flags), "first_anomaly_at": first,
        "latest": {"p95_seconds": latest.latency_seconds,
                   "error_fraction": latest.error_rate,
                   "cpu_ratio": latest.cpu_ratio,
                   "memory_gib": latest.memory_ratio},
        "limitation": "Synthetic-development detector; flags alone do not establish a root cause",
    }
