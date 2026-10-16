"""Record separate healthy training windows for the IsolationForest model."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from pathlib import Path

import httpx
from collect_anomaly import SERVICE_URLS, sample

ROOT = Path(__file__).resolve().parents[1]


def collect() -> list[dict]:
    rows: list[dict] = []
    with httpx.Client() as client:
        for service, url in SERVICE_URLS.items():
            client.delete(url + "/chaos", timeout=5).raise_for_status()
            latency: deque[float] = deque(maxlen=5)
            outcomes: deque[int] = deque(maxlen=5)
            previous_cpu = None
            for index in range(60):
                observation, previous_cpu = sample(
                    client, url, service, latency, outcomes, previous_cpu, None
                )
                rows.append(
                    {
                        "scenario": f"healthy-training-{service}",
                        "index": index,
                        **asdict(observation),
                    }
                )
    (ROOT / "eval" / "anomaly_training.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return rows


if __name__ == "__main__":
    print(f"Recorded {len(collect())} separate healthy training observations")
