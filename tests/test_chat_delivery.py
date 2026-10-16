"""Local chat behavior with fake transports; no real channel is contacted."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from opspilot.bots.chat_actions import acknowledge, draft_from_alert
from opspilot.bots.slack_app import publish_incident
from opspilot.bots.teams_bot import create_bot
from opspilot.storage import AlertEvent, Base, ChatThread, PendingAction


def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    tables = [Base.metadata.tables[name] for name in (
        "alert_events", "chat_threads", "pending_actions"
    )]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add(AlertEvent(
            fingerprint="fp1", alertname="PaymentErrorsInjected", service="payments",
            status="firing", labels={}, annotations={},
            last_received_at=datetime.now(UTC), delivery_count=1,
        ))
        session.commit()
    return engine


def test_chat_buttons_only_draft_and_acknowledge():
    engine = database()
    with Session(engine) as session:
        session.add(ChatThread(platform="slack", fingerprint="fp1",
                               channel_id="C1", message_id="1.0"))
        session.commit()
        assert acknowledge(session, "slack", "fp1", "U1")
        assert not acknowledge(session, "slack", "unknown", "U1")
        assert session.get(ChatThread, ("slack", "fp1")).acknowledged_by == "U1"
        ticket = draft_from_alert(session, "fp1", "ticket")
        change = draft_from_alert(session, "fp1", "change")
        assert ticket["target"].startswith("INC")
        assert change["target"].startswith("CHG")
        assert session.get(PendingAction, ticket["action_id"]).status == "pending"
        assert session.get(PendingAction, change["action_id"]).status == "pending"


class FakeSlackClient:
    def __init__(self):
        self.calls = []

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ts": "1.0"}


def test_slack_publish_is_opt_in_and_idempotent(monkeypatch):
    engine = database()
    monkeypatch.setattr("opspilot.bots.slack_app.make_engine", lambda: engine)
    monkeypatch.delenv("OPSPILOT_CHAT_DELIVERY", raising=False)
    client = FakeSlackClient()
    assert not publish_incident("fp1", {"summary": "Cited [source:RB-01]"}, client)
    monkeypatch.setenv("OPSPILOT_CHAT_DELIVERY", "1")
    monkeypatch.setenv("OPSPILOT_SLACK_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("OPSPILOT_SLACK_CHANNEL", "C1")
    assert publish_incident("fp1", {"summary": "Cited [source:RB-01]"}, client)
    assert not publish_incident("fp1", {"summary": "Cited [source:RB-01]"}, client)
    assert len(client.calls) == 1
    with Session(engine) as session:
        assert session.get(ChatThread, ("slack", "fp1")).message_id == "1.0"


def test_teams_internal_delivery_requires_token(monkeypatch):
    engine = database()
    monkeypatch.setattr("opspilot.bots.teams_bot.make_engine", lambda: engine)
    monkeypatch.setenv("OPSPILOT_TEAMS_INTERNAL_TOKEN", "internal-test")
    monkeypatch.setenv("OPSPILOT_TEAMS_CONVERSATION_ID", "conversation-1")
    monkeypatch.setenv("OPSPILOT_CHAT_DELIVERY", "1")
    web, teams = create_bot(dev_unauthenticated=True)
    sent = []

    async def fake_send(conversation, card):
        sent.append((conversation, card))

    monkeypatch.setattr(teams, "send", fake_send)
    payload = {"fingerprint": "fp1", "result": {"summary": "Cited [source:RB-01]"}}
    with TestClient(web) as client:
        assert client.post("/internal/deliver", json=payload).status_code == 403
        response = client.post(
            "/internal/deliver", json=payload,
            headers={"X-Delivery-Token": "internal-test"},
        )
        assert response.status_code == 200
        assert sent[0][0] == "conversation-1"
        assert "RB-01" in sent[0][1].model_dump_json()
    with Session(engine) as session:
        assert session.get(ChatThread, ("teams", "fp1")).channel_id == "conversation-1"
