"""Inject a demo fault and append ground truth only after the API accepts it."""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from opspilot.faults import FAULT_SERVICE, FaultRecord, append_fault
from opspilot.storage import DeployEvent, make_engine, session_factory

SERVICE_URLS = {
    "gateway": "http://127.0.0.1:18101",
    "payments": "http://127.0.0.1:18102",
    "backups": "http://127.0.0.1:18103",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject a labeled local OpsPilot fault")
    parser.add_argument("name", choices=sorted(FAULT_SERVICE))
    parser.add_argument("--duration", type=int, default=600, help="seconds, 1–3600")
    parser.add_argument("--log", type=Path, default=Path("eval/fault_log.jsonl"))
    args = parser.parse_args()
    if not 1 <= args.duration <= 3600:
        parser.error("duration must be 1–3600 seconds")
    service = FAULT_SERVICE[args.name]
    response = httpx.post(
        f"{SERVICE_URLS[service]}/chaos",
        json={"name": args.name, "duration_seconds": args.duration},
        timeout=5,
    )
    response.raise_for_status()
    start = datetime.now(UTC)
    record = FaultRecord(
        fault_id=f"FAULT-{uuid.uuid4().hex[:12]}",
        name=args.name,
        service=service,
        started_at=start.isoformat(),
        ends_at=(start + timedelta(seconds=args.duration)).isoformat(),
        root_cause=args.name,
    )
    append_fault(args.log, record)
    if args.name == "bad_deploy":
        with session_factory(make_engine())() as session:
            session.add(
                DeployEvent(
                    service=service,
                    version="v1.8.3-simulated-bug",
                    status="failed",
                    occurred_at=start,
                    source_id=record.fault_id,
                )
            )
            session.commit()
    print(record.fault_id, service, args.name, "simulated", sep="\t")


if __name__ == "__main__":
    main()
