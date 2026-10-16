import json
from datetime import UTC, datetime

from opspilot.correlate import EvidenceEvent, correlate
from opspilot.trace_graph import dependencies_from_file


def test_parent_child_cross_service_span_derives_dependency(tmp_path, monkeypatch):
    def resource(service, span):
        return {"resource": {"attributes": [{"key": "service.name",
                    "value": {"stringValue": service}}]},
                "scopeSpans": [{"spans": [span]}]}

    batch = {"resourceSpans": [
        resource("gateway", {"traceId": "t1", "spanId": "s1"}),
        resource("payments", {"traceId": "t1", "spanId": "s2", "parentSpanId": "s1"}),
        resource("backups", {"traceId": "t2", "spanId": "s3"}),
    ]}
    path = tmp_path / "traces.json"
    path.write_text(json.dumps(batch) + "\n")
    assert dependencies_from_file(path) == {"gateway": {"payments"}}
    monkeypatch.setenv("OPSPILOT_TRACE_FILE", str(path))
    now = datetime.now(UTC)
    anchor = EvidenceEvent("alert1", "gateway", "alert", now, "alert:1")
    rows = [anchor, EvidenceEvent("anomaly1", "payments", "anomaly", now,
                                   "prom:error_rate:payments")]
    result = correlate(anchor, rows)
    assert result["dependency_graph_source"] == "observed-traces"
    assert result["services"][0]["service"] == "payments"
