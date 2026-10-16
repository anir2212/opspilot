"""Slack Socket Mode follow-ups and credential-gated incident publication."""

from __future__ import annotations

import os
import re

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from sqlalchemy import select

from opspilot.bots.cards import slack_message
from opspilot.bots.chat_actions import acknowledge, draft_from_alert
from opspilot.storage import ChatThread, make_engine, session_factory


def publish_incident(fingerprint: str, result: dict, client: WebClient | None = None) -> bool:
    """Post only when explicitly enabled and credentialed; persist the thread mapping."""
    if os.getenv("OPSPILOT_CHAT_DELIVERY") != "1":
        return False
    token = os.getenv("OPSPILOT_SLACK_BOT_TOKEN", "")
    channel = os.getenv("OPSPILOT_SLACK_CHANNEL", "")
    if not token or not channel:
        return False
    Session = session_factory(make_engine())
    with Session() as session:
        if session.get(ChatThread, ("slack", fingerprint)):
            return False
    sdk = client or WebClient(token=token)
    response = sdk.chat_postMessage(channel=channel, **slack_message(fingerprint, result))
    with Session() as session:
        session.add(ChatThread(
            platform="slack", fingerprint=fingerprint,
            channel_id=channel, message_id=str(response["ts"]),
        ))
        session.commit()
    return True


def build_app(*, bot_token: str | None = None, api_url: str | None = None) -> App:
    token = bot_token or os.getenv("OPSPILOT_SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("OPSPILOT_SLACK_BOT_TOKEN is required")
    api = api_url or os.getenv("OPSPILOT_API_URL", "http://127.0.0.1:18100")
    app = App(token=token)
    Session = session_factory(make_engine())

    @app.message(re.compile(r".+", re.DOTALL))
    def followup(message, say):
        if message.get("bot_id") or message.get("subtype") or not message.get("thread_ts"):
            return
        with Session() as session:
            thread = session.scalar(
                select(ChatThread).where(
                    ChatThread.platform == "slack",
                    ChatThread.channel_id == message["channel"],
                    ChatThread.message_id == message["thread_ts"],
                )
            )
            fingerprint = thread.fingerprint if thread else None
        if fingerprint is None:
            return
        question = str(message.get("text", "")).strip()
        if len(question) < 3:
            return
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{api}/api/triage/{fingerprint}/followup", json={"question": question}
            )
            response.raise_for_status()
        say(text=response.json()["answer"], thread_ts=message["thread_ts"])

    def action_handler(action, body, ack, respond, kind: str):
        ack()
        fingerprint = str(action.get("value", ""))
        actor = str(body.get("user", {}).get("id", "unknown"))
        try:
            with Session() as session:
                if kind == "ack":
                    ok = acknowledge(session, "slack", fingerprint, actor)
                    text = "Incident acknowledged." if ok else "Unknown incident thread."
                else:
                    draft = draft_from_alert(session, fingerprint, kind)
                    text = (
                        f"Pending {kind} {draft['target']} ({draft['action_id']}). "
                        "A separate operator approval is required in the web console."
                    )
        except ValueError as exc:
            text = str(exc)
        respond(text=text, response_type="ephemeral")

    @app.action("opspilot_ack")
    def ack_incident(action, body, ack, respond):
        action_handler(action, body, ack, respond, "ack")

    @app.action("opspilot_open_ticket")
    def draft_ticket(action, body, ack, respond):
        action_handler(action, body, ack, respond, "ticket")

    @app.action("opspilot_draft_change")
    def draft_change(action, body, ack, respond):
        action_handler(action, body, ack, respond, "change")

    return app


def main() -> None:
    app_token = os.getenv("OPSPILOT_SLACK_APP_TOKEN", "")
    if not app_token:
        raise ValueError("OPSPILOT_SLACK_APP_TOKEN is required for Socket Mode")
    SocketModeHandler(build_app(), app_token).start()


if __name__ == "__main__":
    main()
