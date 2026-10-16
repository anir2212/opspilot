"""End-to-end proof that each fault reaches OpsPilot through the monitoring stack."""

from __future__ import annotations

import os
import time

import httpx
import pytest

from opspilot.faults import FAULT_SERVICE

SERVICE_URLS = {
    "gateway": "http://127.0.0.1:18101",
    "payments": "http://127.0.0.1:18102",
    "backups": "http://127.0.0.1:18103",
}
EXPECTED_ALERTS = {
    "payment_errors": "PaymentErrorsInjected",
    "latency": "LatencyInjected",
    "memory_leak": "MemoryLeakInjected",
    "cpu_saturation": "CPUSaturationInjected",
    "dependency_down": "DependencyDownInjected",
    "bad_deploy": "BadDeployInjected",
    "backup_failure": "BackupFailureInjected",
    "disk_full": "DiskFullInjected",
}


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("OPSPILOT_INTEGRATION") != "1", reason="set OPSPILOT_INTEGRATION=1 with compose up"
)
def test_all_eight_faults_arrive_at_webhook_within_ninety_seconds() -> None:
    with httpx.Client(timeout=10) as client:
        for service_url in SERVICE_URLS.values():
            client.delete(f"{service_url}/chaos").raise_for_status()
        try:
            for name, service in FAULT_SERVICE.items():
                client.post(
                    f"{SERVICE_URLS[service]}/chaos",
                    json={"name": name, "duration_seconds": 120},
                ).raise_for_status()
            deadline = time.monotonic() + 90
            seen: set[str] = set()
            while time.monotonic() < deadline:
                notifications = client.get("http://127.0.0.1:18100/api/alerts").json()
                seen = {
                    alert["labels"]["alertname"]
                    for notification in notifications
                    for alert in notification["alerts"]
                    if alert["status"] == "firing"
                }
                if set(EXPECTED_ALERTS.values()) <= seen:
                    break
                time.sleep(2)
            assert set(EXPECTED_ALERTS.values()) <= seen, set(EXPECTED_ALERTS.values()) - seen
        finally:
            for service_url in SERVICE_URLS.values():
                client.delete(f"{service_url}/chaos").raise_for_status()
