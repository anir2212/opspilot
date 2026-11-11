import json

from opspilot.agent import _claim_prompt
from opspilot.context_view import prompt_context


def test_prompt_keeps_distinct_runbooks_after_large_metric_series() -> None:
    alert = {"fingerprint": "case-1", "labels": {"service": "payments"},
             "annotations": {"description": "payment requests fail with 5xx"}}
    context = {
        "error_rate": {"source_id": "prom:error_rate:payments", "series": [
            {"values": [[index, "0.7"] for index in range(2000)]}
        ]},
        "runbooks": {"source_id": "knowledge:runbooks", "results": [
            {"source_id": "RB-01", "body": "payment error check " * 100},
            {"source_id": "RB-01", "body": "repeated chunk"},
            {"source_id": "RB-02", "body": "latency check"},
        ]},
        "backups": {"source_id": "backup:jobs", "jobs": [
            {"id": 9, "status": "failed", "error_code": "unrelated"}
        ]},
    }
    view = prompt_context(alert, context)
    assert [row["source_id"] for row in view["runbooks"]["results"]] == ["RB-01", "RB-02"]
    assert "backups" not in view
    prompt = _claim_prompt("List causes", alert, context)
    assert "RB-01" in prompt and "RB-02" in prompt
    assert "unrelated" not in prompt
    assert len(prompt) < 6500
    assert "0.7" in prompt


def test_prompt_scopes_backup_evidence_and_preserves_source_ids() -> None:
    alert = {"fingerprint": "case-2", "labels": {"service": "backups"},
             "annotations": {"description": "backup failed"}}
    context = {"backups": {"source_id": "backup:jobs", "jobs": [
        {"id": 1, "status": "failed", "error_code": "DISK_FULL"}
    ]}}
    view = prompt_context(alert, context)
    assert view["backups"]["jobs"][0]["error_code"] == "DISK_FULL"
    prompt = _claim_prompt("List causes", alert, context)
    assert "backup:1" in prompt
    assert "DISK_FULL" in prompt
    assert json.dumps(view) in prompt
