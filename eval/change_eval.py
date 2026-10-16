"""Report agreement with ten authored change-policy examples."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from opspilot.change_approval import DeploymentRequest, ReviewContext, evaluate_change

start = datetime(2026, 10, 11, 1, tzinfo=UTC)
base = {
    "change_id": "CHG1001",
    "service": "payments",
    "version": "v2.0.0",
    "window_start": start,
    "window_end": start + timedelta(hours=1),
    "description": "Deploy payment service safely",
    "test_evidence": ["local-test-report.json"],
    "signoffs": {"qa": "Ada", "owner": "Lin", "security": "Sam"},
}
cases = [
    ("complete", {}, {}, "APPROVE"),
    ("missing tests", {"test_evidence": []}, {}, "NEEDS INFO"),
    ("missing security", {"signoffs": {"qa": "Ada", "owner": "Lin"}}, {}, "NEEDS INFO"),
    ("missing owner", {"signoffs": {"qa": "Ada", "security": "Sam"}}, {}, "NEEDS INFO"),
    ("missing QA", {"signoffs": {"owner": "Lin", "security": "Sam"}}, {}, "NEEDS INFO"),
    ("freeze", {}, {"frozen": True}, "REJECT"),
    ("severe incident", {}, {"open_severe_incidents": ["INC1"]}, "REJECT"),
    ("failed deploy", {}, {"recent_failed_deploys": ["D1"]}, "REJECT"),
    ("invalid window", {"window_end": start - timedelta(minutes=1)}, {}, "REJECT"),
    ("missing calendar", {}, {"calendar_verified": False}, "NEEDS INFO"),
]


def run() -> dict:
    rows = []
    for name, request_changes, context_changes, expected in cases:
        request = DeploymentRequest(**(base | request_changes))
        context = ReviewContext(**({"calendar_verified": True} | context_changes))
        result = evaluate_change(request, context)
        rows.append({"case": name, "expected": expected, "actual": result.decision.value,
                     "agree": result.decision.value == expected})
    output = {"method": "authored policy fixture agreement, not real change outcome accuracy",
              "cases": len(rows), "agreements": sum(row["agree"] for row in rows), "rows": rows}
    destination = Path(__file__).with_name("change_results.json")
    destination.write_text(json.dumps(output, indent=2) + "\n")
    return output


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
