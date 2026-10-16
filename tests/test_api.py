from fastapi.testclient import TestClient

from opspilot.api import create_app


def test_alertmanager_webhook_accepts_and_lists_alerts() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/alerts",
            json={
                "status": "firing",
                "receiver": "opspilot",
                "alerts": [{"status": "firing", "labels": {"alertname": "PaymentErrors"}}],
            },
        )
        assert response.status_code == 202
        assert response.json() == {"accepted": 1}
        alert = client.get("/api/alerts").json()[0]["alerts"][0]
        assert alert["labels"]["alertname"] == "PaymentErrors"


def test_alertmanager_webhook_rejects_invalid_status() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/alerts", json={"status": "bad", "alerts": []}).status_code == 422
