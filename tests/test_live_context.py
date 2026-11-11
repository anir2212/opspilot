import json
from datetime import UTC, datetime

import httpx
import pytest

from opspilot.agent import GeminiBackend, _available_sources
from opspilot.live_anomaly import score_live_series
from opspilot.tools import AnomalyQuery, InvestigationTools, TraceQuery


def test_live_anomaly_uses_only_pre_incident_training_points() -> None:
    start = 1_800_000_000
    data = {}
    for name, normal, incident in (
        ("p95_latency", 0.05, 3.0),
        ("p50_latency", 0.03, 2.0),
        ("error_rate", 0.0, 0.8),
        ("cpu_usage", 0.01, 0.1),
        ("memory_bytes", 100 * 1024**2, 100 * 1024**2),
    ):
        values = [[start + index * 5, str(normal if index < 60 else incident)]
                  for index in range(70)]
        data[name] = [{"values": values}]
    result = score_live_series(
        AnomalyQuery(service="payments", incident_start=datetime.fromtimestamp(start + 300, UTC)),
        data,
    )
    assert result["available"] is True
    assert result["baseline_samples"] == 60
    assert result["incident_samples"] == 10
    assert result["anomalous_samples"] == 10
    assert result["source_id"] == "anomaly:payments"
    assert score_live_series(AnomalyQuery(service="payments"), data)["available"] is False


def test_tempo_query_returns_bounded_safe_span_evidence() -> None:
    trace_id = "a" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            assert request.url.params["tags"] == "service.name=payments"
            assert request.url.params["limit"] == "5"
            return httpx.Response(200, json={"traces": [{"traceID": trace_id,
                "rootServiceName": "gateway", "durationMs": 33,
                "startTimeUnixNano": "1800000000000000000"}]})
        assert request.url.path == f"/api/traces/{trace_id}"
        return httpx.Response(200, json={"resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "payments"}}
            ]},
            "scopeSpans": [{"spans": [{"name": "GET /work", "status": {"code": "ERROR"},
                "attributes": [
                    {"key": "http.response.status_code", "value": {"intValue": "503"}},
                    {"key": "private.secret", "value": {"stringValue": "hidden"}},
                ]}]}],
        }]})

    tools = InvestigationTools(tempo_url="http://tempo:3200",
                               client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = tools.query_traces(TraceQuery(service="payments"))
    assert result["traces"][0]["spans"][0]["attributes"] == {"http.response.status_code": "503"}
    assert "hidden" not in str(result)
    assert f"tempo:trace:{trace_id}" in _available_sources({}, {"traces": result})


def test_gemini_backend_is_opt_in_and_preserves_citation_ids(monkeypatch) -> None:
    monkeypatch.delenv("OPSPILOT_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiBackend()
    monkeypatch.setenv("OPSPILOT_GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("OPSPILOT_GEMINI_THINKING_LEVEL", "invalid")
    with pytest.raises(ValueError, match="THINKING_LEVEL"):
        GeminiBackend()
    monkeypatch.setenv("OPSPILOT_GEMINI_THINKING_LEVEL", "low")
    monkeypatch.setattr("opspilot.agent.time.sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["x-goog-api-key"] == "test-only-key"
        assert "key=" not in str(request.url)
        assert request.url.path.endswith(":generateContent")
        payload = json.loads(request.read())
        response_text = payload["generationConfig"]["responseFormat"]["text"]
        assert response_text["mimeType"] == "APPLICATION_JSON"
        assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": (
                '{"claims":[{"text":"Inspect the payment errors","source_ids":["RB-01"]}]}'
            )}]}}],
            "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 10},
        })

    backend = GeminiBackend(httpx.Client(transport=httpx.MockTransport(handler)))
    claims = backend.recommend({}, {"runbooks": {"results": [{"source_id": "RB-01"}]}}, [])
    assert claims[0]["source_ids"] == ["RB-01"]
    assert (backend.input_tokens, backend.output_tokens) == (30, 10)
    assert calls == 2
