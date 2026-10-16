from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from opspilot.api import create_app
from opspilot.mocks import get_session
from opspilot.storage import BackupJob, ChangeRequest, Incident


def test_local_itsm_and_backup_contracts() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in (Incident.__table__, ChangeRequest.__table__, BackupJob.__table__):
        table.create(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        incident = {
            "number": "INC9001",
            "short_description": "Payment test incident",
            "cmdb_ci": "payments",
        }
        assert client.post("/api/now/table/incident", json=incident).status_code == 201
        assert client.post("/api/now/table/incident", json=incident).status_code == 409
        assert (
            client.get("/api/now/table/incident?cmdb_ci=payments").json()["result"][0]["number"]
            == "INC9001"
        )
        change = {
            "number": "CHG9001",
            "short_description": "Approve local rollback",
            "cmdb_ci": "payments",
        }
        assert client.post("/api/now/table/change_request", json=change).status_code == 201
        assert client.get("/api/now/table/change_request").json()["result"][0]["state"] == "draft"
        job = {
            "policy": "daily",
            "client": "backups",
            "status": "failed",
            "bytes_written": 0,
            "error_code": "NO_SPACE",
        }
        assert client.post("/api/backups/jobs", json=job).status_code == 201
        assert len(client.get("/api/backups/jobs?status=failed").json()["jobs"]) == 1
        assert client.get("/api/backups/jobs?status=success").json()["jobs"] == []
