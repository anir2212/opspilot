"""The five-scenario CI slice checks harness contracts, not model quality."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

from opspilot.citation_review import review_items, summarize_review, write_review_queue


def test_forty_scenarios_cover_all_variants_and_five_run():
    root = Path(__file__).resolve().parents[1]
    scenarios = [
        yaml.safe_load(path.read_text())
        for path in sorted((root / "eval" / "scenarios").glob("*.yaml"))
    ]
    assert len(scenarios) == 40
    assert Counter(scenario["variant"] for scenario in scenarios) == {
        "baseline": 8, "noisy": 8, "concurrent": 8, "sparse": 8, "false_alarm": 8
    }
    assert all(len(scenario["expected_faults"]) == 2 for scenario in scenarios
               if scenario["variant"] == "concurrent")
    assert all(not scenario["expected_faults"] for scenario in scenarios
               if scenario["variant"] == "false_alarm")
    result = subprocess.run(
        [sys.executable, "eval/run_eval.py", "--backend", "fixture", "--limit", "5"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    assert report["agent"]["scenario_count"] == 5
    assert report["agent"]["mean_tool_calls_per_scenario"] == 7
    assert report["agent"]["citation_semantic_precision"] is None


def test_citation_review_preserves_evidence_and_requires_manual_labels(tmp_path):
    alert = {"fingerprint": "incident-1", "labels": {"alertname": "PaymentErrorsInjected"}}
    output = {
        "hypotheses": [{"text": "Payments are failing", "source_ids": ["alert:incident-1"]}],
        "recommendations": [{"text": "Read the runbook", "source_ids": ["RB-01"]}],
        "context": {"runbooks": {"source_id": "fixture:runbooks", "results": [
            {"source_id": "RB-01", "body": "Verify payment errors before remediation."}
        ]}},
    }
    items = review_items("case-1", "agent", alert, output)
    assert [item["source_evidence"] for item in items] == [
        alert, {"source_id": "RB-01", "body": "Verify payment errors before remediation."}
    ]
    path = tmp_path / "review.jsonl"
    write_review_queue(path, items)
    assert summarize_review(path) == {
        "pairs": 2, "labeled_pairs": 0, "semantic_citation_precision": None
    }
    labeled = [dict(items[0], supports_claim=True), dict(items[1], supports_claim=False)]
    write_review_queue(path, labeled)
    assert summarize_review(path)["semantic_citation_precision"] == 0.5
