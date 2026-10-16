"""Fault labels, target services, and durable ground-truth logging."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

FAULT_SERVICE = {
    "payment_errors": "payments",
    "latency": "gateway",
    "memory_leak": "gateway",
    "cpu_saturation": "gateway",
    "dependency_down": "payments",
    "bad_deploy": "payments",
    "backup_failure": "backups",
    "disk_full": "backups",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class FaultRecord:
    fault_id: str
    name: str
    service: str
    started_at: str
    ends_at: str
    root_cause: str
    simulated: bool = True


def append_fault(path: Path, record: FaultRecord) -> None:
    """Write an independently inspectable label for later evaluation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def read_faults(path: Path) -> list[FaultRecord]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [FaultRecord(**json.loads(line)) for line in handle if line.strip()]
