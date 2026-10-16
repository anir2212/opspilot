"""Ten fixed policy cases; these are authored expectations, not model accuracy."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from opspilot.api import create_app
from opspilot.change_approval import (
    Decision,
    DeploymentRequest,
    ReviewContext,
    evaluate_change,
)
from opspilot.mocks import get_session
from opspilot.storage import Base

NOW = datetime(2026, 10, 11, 1, tzinfo=UTC)


def request(**changes) -> DeploymentRequest:
    values = {
        "change_id": "CHG1001", "service": "payments", "version": "v2.0.0",
        "window_start": NOW, "window_end": NOW + timedelta(hours=1),
        "description": "Deploy payment service safely",
        "test_evidence": ["local-test-report.json"],
        "signoffs": {"qa": "Ada", "owner": "Lin", "security": "Sam"},
    }
    values.update(changes)
    return DeploymentRequest(**values)


@pytest.mark.parametrize(
    ("request_changes", "context_changes", "expected"),
    [
        ({}, {}, Decision.APPROVE),
        ({"test_evidence": []}, {}, Decision.NEEDS_INFO),
        ({"signoffs": {"qa": "Ada", "owner": "Lin"}}, {}, Decision.NEEDS_INFO),
        ({"signoffs": {"qa": "Ada", "security": "Sam"}}, {}, Decision.NEEDS_INFO),
        ({"signoffs": {"owner": "Lin", "security": "Sam"}}, {}, Decision.NEEDS_INFO),
        ({}, {"frozen": True}, Decision.REJECT),
        ({}, {"open_severe_incidents": ["alert-1"]}, Decision.REJECT),
        ({}, {"recent_failed_deploys": ["deploy-1"]}, Decision.REJECT),
        ({"window_end": NOW - timedelta(minutes=1)}, {}, Decision.REJECT),
        ({}, {"calendar_verified": False}, Decision.NEEDS_INFO),
    ],
)
def test_policy_cases(request_changes, context_changes, expected):
    context = ReviewContext(**({"calendar_verified": True} | context_changes))
    assert evaluate_change(request(**request_changes), context).decision == expected


def test_signoff_tracking_requires_token_and_re_evaluates(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["change_reviews"],
                                         Base.metadata.tables["alert_events"],
                                         Base.metadata.tables["deploy_events"]])
    calendar = tmp_path / "freezes.json"
    calendar.write_text('{"windows": []}')
    monkeypatch.setenv("OPSPILOT_FREEZE_CALENDAR", str(calendar))
    monkeypatch.setenv("OPSPILOT_APPROVAL_TOKEN", "local-test-secret")
    app = create_app()

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        payload = request(signoffs={"qa": "Ada", "owner": "Lin"}).model_dump(mode="json")
        created = client.post("/api/change-reviews", json=payload)
        assert created.status_code == 201
        assert created.json()["decision"] == "NEEDS INFO"
        denied = client.post(
            "/api/change-reviews/CHG1001/signoffs", json={"role": "security", "signer": "Sam"}
        )
        assert denied.status_code == 403
        signed = client.post(
            "/api/change-reviews/CHG1001/signoffs",
            json={"role": "security", "signer": "Sam"},
            headers={"X-Approval-Token": "local-test-secret"},
        )
        assert signed.status_code == 200
        assert signed.json()["decision"] == "APPROVE"
        stored = client.get("/api/change-reviews/CHG1001").json()
        assert stored["request"]["signoffs"]["security"] == "Sam"
