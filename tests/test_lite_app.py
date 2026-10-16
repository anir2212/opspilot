from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from opspilot.faults import FAULT_SERVICE
from opspilot.lite_app import create_app


def sample_value(metrics: str, name: str, labels: dict[str, str]) -> float:
    for family in text_string_to_metric_families(metrics):
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return float(sample.value)
    raise AssertionError(f"metric {name} with {labels} was absent")


def test_each_fault_is_contained_and_visible_in_metrics() -> None:
    for name, service in FAULT_SERVICE.items():
        with TestClient(create_app(service)) as client:
            injected = client.post("/chaos", json={"name": name, "duration_seconds": 30})
            assert injected.status_code == 200
            metrics = client.get("/metrics").text
            assert (
                sample_value(metrics, "opspilot_fault_active", {"service": service, "kind": name})
                == 1
            )
            assert client.delete("/chaos").status_code == 200
            assert (
                sample_value(
                    client.get("/metrics").text,
                    "opspilot_fault_active",
                    {"service": service, "kind": name},
                )
                == 0
            )


def test_payment_fault_changes_work_result() -> None:
    with TestClient(create_app("payments")) as client:
        assert client.get("/work").status_code == 200
        client.post("/chaos", json={"name": "payment_errors", "duration_seconds": 30})
        assert client.get("/work").status_code == 503
        assert (
            sample_value(
                client.get("/metrics").text,
                "opspilot_requests_total",
                {"service": "payments", "outcome": "error"},
            )
            == 1
        )


def test_reject_wrong_service_and_invalid_duration() -> None:
    with TestClient(create_app("gateway")) as client:
        wrong_service = client.post(
            "/chaos", json={"name": "payment_errors", "duration_seconds": 30}
        )
        invalid_duration = client.post("/chaos", json={"name": "latency", "duration_seconds": 0})
        assert wrong_service.status_code == 400
        assert invalid_duration.status_code == 422
