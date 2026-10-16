from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from opspilot.actions import create_pending
from opspilot.api import create_app
from opspilot.mocks import get_session
from opspilot.storage import ChangeRequest, Incident, PendingAction


def test_ticket_draft_requires_explicit_approval_and_is_single_use(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in (Incident.__table__, ChangeRequest.__table__, PendingAction.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add(
            Incident(
                number="INC9002",
                short_description="Payment errors",
                cmdb_ci="payments",
                work_notes="original",
                close_notes="",
                state="new",
            )
        )
        session.commit()
        action = create_pending(session, "ticket_update", "INC9002", {"text": "Investigate RB-01"})
        action_id = action.id
        assert session.get(Incident, "INC9002").work_notes == "original"

    def session_override():
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    monkeypatch.setenv("OPSPILOT_APPROVAL_TOKEN", "test-secret")
    with TestClient(app) as client:
        url = f"/api/pending-actions/{action_id}/approve"
        assert client.post(url).status_code == 403
        assert client.post(url, headers={"X-Approval-Token": "wrong"}).status_code == 403
        assert client.get("/api/pending-actions").json()["actions"][0]["status"] == "pending"
        approved = client.post(url, headers={"X-Approval-Token": "test-secret"})
        assert approved.status_code == 200
        assert approved.json()["action"]["status"] == "approved"
        assert client.post(url, headers={"X-Approval-Token": "test-secret"}).status_code == 409
    with Session(engine) as session:
        assert session.get(Incident, "INC9002").work_notes == "original\nInvestigate RB-01"


def test_missing_config_disables_approval(monkeypatch) -> None:
    monkeypatch.delenv("OPSPILOT_APPROVAL_TOKEN", raising=False)
    with TestClient(create_app()) as client:
        assert client.post("/api/pending-actions/unknown/approve").status_code == 503
