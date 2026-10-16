from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from opspilot.api import create_app
from opspilot.storage import AlertEvent, BackupJob, DeployEvent


def test_alertmanager_retry_updates_one_persisted_incident() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    AlertEvent.__table__.create(engine)
    DeployEvent.__table__.create(engine)
    BackupJob.__table__.create(engine)
    app = create_app()
    app.state.Session = sessionmaker(engine)
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "PaymentErrorsInjected", "service": "payments"},
                "fingerprint": "same-alert",
            }
        ],
    }
    with TestClient(app) as client:
        assert client.post("/api/alerts", json=payload).status_code == 202
        assert client.post("/api/alerts", json=payload).status_code == 202
        records = client.get("/api/incidents").json()["incidents"]
        assert len(records) == 1
        assert records[0]["delivery_count"] == 2
        payload["status"] = "resolved"
        payload["alerts"][0]["status"] = "resolved"
        assert client.post("/api/alerts", json=payload).status_code == 202
        assert client.get("/api/incidents").json()["incidents"][0]["status"] == "resolved"
        with Session(engine) as session:
            session.add(DeployEvent(service="payments", version="v2", status="failed",
                                    occurred_at=datetime.now(UTC), source_id="deploy-test"))
            session.commit()
        timeline = client.get("/api/incidents/same-alert/timeline").json()["events"]
        assert {event["source_id"] for event in timeline} == {
            "alert:same-alert", "deploy-test"
        }
