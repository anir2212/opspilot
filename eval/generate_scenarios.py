"""Write 40 fixed, inspectable evaluation fixtures without model-generated labels."""

from __future__ import annotations

from pathlib import Path

import yaml

FAULTS = [
    ("payment_errors", "payments", "RB-01", "payment requests fail with 5xx responses"),
    ("latency", "gateway", "RB-02", "gateway p95 latency rose above two seconds"),
    ("memory_leak", "gateway", "RB-03", "gateway resident memory keeps growing"),
    ("cpu_saturation", "gateway", "RB-04", "gateway CPU is saturated"),
    ("dependency_down", "payments", "RB-05", "payments cannot reach its dependency"),
    ("bad_deploy", "payments", "RB-06", "payment failures began after a release"),
    ("backup_failure", "backups", "RB-07", "scheduled backup job failed"),
    ("disk_full", "backups", "RB-08", "backup capacity is exhausted"),
]
VARIANTS = ("baseline", "noisy", "concurrent", "sparse", "false_alarm")
SCENARIOS = Path(__file__).with_name("scenarios")


def generate() -> list[Path]:
    SCENARIOS.mkdir(exist_ok=True)
    files = []
    for index, (fault, service, runbook, signal) in enumerate(FAULTS):
        other_fault, _, other_runbook, _ = FAULTS[(index + 1) % len(FAULTS)]
        for variant in VARIANTS:
            expected_faults = [] if variant == "false_alarm" else [fault]
            expected_runbooks = [] if variant == "false_alarm" else [runbook]
            if variant == "concurrent":
                expected_faults.append(other_fault)
                expected_runbooks.append(other_runbook)
            description = (
                f"Transient signal resembling {signal}; recovered before sustained impact"
                if variant == "false_alarm" else signal
            )
            candidates = ["RB-12", runbook, other_runbook]
            if variant == "noisy":
                candidates = [other_runbook, "RB-12", runbook]
            scenario = {
                "id": f"{fault}_{variant}",
                "source": "authored fixture; no live fault injected by this file",
                "variant": variant,
                "alert": {
                    "fingerprint": f"fixture-{fault}-{variant}",
                    "status": "firing",
                    "labels": {"alertname": "ServiceDegraded", "service": service},
                    "annotations": {"description": description},
                },
                "expected_faults": expected_faults,
                "expected_runbooks": expected_runbooks,
                "evidence": {
                    "signal": description,
                    "noise": "unrelated retries also observed" if variant == "noisy" else "",
                    "incomplete": variant == "sparse",
                },
                "runbook_candidates": candidates,
            }
            path = SCENARIOS / f"{fault}_{variant}.yaml"
            path.write_text(yaml.safe_dump(scenario, sort_keys=False))
            files.append(path)
    return files


if __name__ == "__main__":
    print(f"Wrote {len(generate())} scenarios")
