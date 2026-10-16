"""Score 32 recorded fault episodes without using the injected-fault gauge as a feature."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

from opspilot.anomaly import (
    MultivariateIsolationForest,
    Observation,
    STLMedianAbsoluteDeviation,
    detection_metrics,
)

ROOT = Path(__file__).resolve().parents[1]


def evaluate(rows: list[dict], training_rows: list[dict], fault_starts: dict[str, str]) -> dict:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    healthy_by_service: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        observation = Observation(**{key: row[key] for key in Observation.__dataclass_fields__})
        grouped[row["scenario"]].append(observation)
    for row in training_rows:
        observation = Observation(**{key: row[key] for key in Observation.__dataclass_fields__})
        if observation.label is not None:
            raise ValueError("Training rows must be healthy")
        healthy_by_service[observation.service].append(observation)

    forests = {
        service: MultivariateIsolationForest().fit(healthy)
        for service, healthy in healthy_by_service.items()
    }
    labels: list[bool] = []
    detector_flags: list[bool] = []
    static_flags: list[bool] = []
    detector_delays: list[float] = []
    static_delays: list[float] = []
    detected_episodes = 0
    static_episodes = 0
    for scenario, series in grouped.items():
        baseline = [row for row in series if row.label is None]
        latency = [row.latency_seconds for row in series]
        errors = [row.error_rate for row in series]
        stl_latency = STLMedianAbsoluteDeviation().fit([row.latency_seconds for row in baseline])
        stl_error = STLMedianAbsoluteDeviation().fit([row.error_rate for row in baseline])
        lat_flags = stl_latency.flags(latency)
        error_flags = stl_error.flags(errors)
        forest_flags = forests[series[0].service].flags(series)
        predicted = [
            a or b or c for a, b, c in zip(lat_flags, error_flags, forest_flags, strict=True)
        ]
        thresholded = [row.error_rate > 0.2 or row.latency_seconds > 2.0 for row in series]
        actual = [row.label is not None for row in series]
        labels.extend(actual)
        detector_flags.extend(predicted)
        static_flags.extend(thresholded)
        start_index = next(index for index, label in enumerate(actual) if label)
        start_time = datetime.fromisoformat(fault_starts[scenario])
        for flags, delays in ((predicted, detector_delays), (thresholded, static_delays)):
            first = next((i for i in range(start_index, len(flags)) if flags[i]), None)
            if first is not None:
                delays.append(
                    (datetime.fromisoformat(series[first].timestamp) - start_time).total_seconds()
                )
        detected_episodes += any(predicted[start_index:])
        static_episodes += any(thresholded[start_index:])
    return {
        "source": "locally injected synthetic faults; observed HTTP and process metrics",
        "episodes": len(grouped),
        "samples": len(labels),
        "separate_healthy_training_samples": len(training_rows),
        "detector": {
            **detection_metrics(labels, detector_flags),
            "episodes_detected": detected_episodes,
            "median_detection_delay_seconds_detected_only": median(detector_delays)
            if detector_delays
            else None,
        },
        "static_threshold": {
            **detection_metrics(labels, static_flags),
            "episodes_detected": static_episodes,
            "median_detection_delay_seconds_detected_only": median(static_delays)
            if static_delays
            else None,
        },
    }


if __name__ == "__main__":
    source = ROOT / "eval" / "anomaly_observations.jsonl"
    rows = [json.loads(line) for line in source.read_text().splitlines() if line]
    training_source = ROOT / "eval" / "anomaly_training.jsonl"
    training_rows = [json.loads(line) for line in training_source.read_text().splitlines() if line]
    fault_records = [
        json.loads(line)
        for line in (ROOT / "eval" / "anomaly_fault_log.jsonl").read_text().splitlines()
        if line
    ]
    variants: dict[str, int] = defaultdict(int)
    fault_starts: dict[str, str] = {}
    for record in fault_records:
        variants[record["name"]] += 1
        fault_starts[f"{record['name']}-{variants[record['name']]}"] = record["started_at"]
    report = evaluate(rows, training_rows, fault_starts)
    (ROOT / "eval" / "anomaly_results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
