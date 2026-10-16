"""Record 32 real local fault-injection episodes and observed service responses."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

from opspilot.anomaly import Observation
from opspilot.faults import FAULT_SERVICE, FaultRecord, append_fault

ROOT = Path(__file__).resolve().parents[1]
SERVICE_URLS = {
    "gateway": "http://127.0.0.1:18101",
    "payments": "http://127.0.0.1:18102",
    "backups": "http://127.0.0.1:18103",
}


def metric_value(payload: str, name: str) -> float:
    for line in payload.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[-1])
    return 0.0


def sample(
    client: httpx.Client,
    base_url: str,
    service: str,
    history: deque[float],
    outcomes: deque[int],
    previous_cpu: float | None,
    label: str | None,
) -> tuple[Observation, float]:
    start = time.monotonic()
    response = client.get(base_url + "/work", timeout=7)
    elapsed = time.monotonic() - start
    metrics = client.get(base_url + "/metrics", timeout=5).text
    cpu_total = metric_value(metrics, "process_cpu_seconds_total")
    rss = metric_value(metrics, "process_resident_memory_bytes")
    history.append(elapsed)
    outcomes.append(int(response.status_code >= 400))
    observation = Observation(
        timestamp=datetime.now(UTC).isoformat(),
        service=service,
        latency_seconds=float(np.percentile(history, 95)),
        error_rate=sum(outcomes) / len(outcomes),
        cpu_ratio=max(0.0, (cpu_total - previous_cpu) / max(elapsed, 0.01))
        if previous_cpu is not None
        else 0.0,
        memory_ratio=rss / (1024**3),
        restarts=0.0,
        label=label,
        latency_p50_seconds=float(np.percentile(history, 50)),
    )
    return observation, cpu_total


def collect() -> list[dict]:
    rows: list[dict] = []
    fault_log = ROOT / "eval" / "anomaly_fault_log.jsonl"
    fault_log.unlink(missing_ok=True)
    with httpx.Client() as client:
        for name, service in FAULT_SERVICE.items():
            url = SERVICE_URLS[service]
            for variant in range(4):
                scenario = f"{name}-{variant + 1}"
                client.delete(url + "/chaos", timeout=5).raise_for_status()
                history: deque[float] = deque(maxlen=5)
                outcomes: deque[int] = deque(maxlen=5)
                previous_cpu = None
                for index in range(16):
                    observation, previous_cpu = sample(
                        client, url, service, history, outcomes, previous_cpu, None
                    )
                    rows.append({"scenario": scenario, "index": index, **asdict(observation)})
                client.post(
                    url + "/chaos", json={"name": name, "duration_seconds": 60}, timeout=5
                ).raise_for_status()
                started = datetime.now(UTC)
                try:
                    for index in range(16, 22):
                        observation, previous_cpu = sample(
                            client, url, service, history, outcomes, previous_cpu, name
                        )
                        rows.append({"scenario": scenario, "index": index, **asdict(observation)})
                finally:
                    client.delete(url + "/chaos", timeout=5).raise_for_status()
                record = FaultRecord(
                    fault_id=f"FAULT-{uuid.uuid4().hex[:12]}",
                    name=name,
                    service=service,
                    started_at=started.isoformat(),
                    ends_at=datetime.now(UTC).isoformat(),
                    root_cause=name,
                )
                append_fault(fault_log, record)
                print(f"Collected {scenario}: {len(rows)} cumulative samples", flush=True)
    target = ROOT / "eval" / "anomaly_observations.jsonl"
    target.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    print(f"Recorded {len(collect())} labeled observations")
