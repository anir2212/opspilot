"""Pure Slack and Teams incident views; no network or approval side effects."""

from __future__ import annotations

from microsoft_teams.cards import AdaptiveCard, ExecuteAction, TextBlock


def _summary(result: dict) -> str:
    return str(result.get("summary") or "Investigation is still in progress.")[:3000]


def slack_message(fingerprint: str, result: dict) -> dict:
    """Block Kit message; buttons open local operator paths and never approve a write."""
    summary = _summary(result)
    return {
        "text": f"OpsPilot incident {fingerprint}: {summary[:150]}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "OpsPilot incident"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"Thread `{fingerprint}`"}
            ]},
            {"type": "actions", "elements": [
                {"type": "button", "action_id": "opspilot_ack", "text": {
                    "type": "plain_text", "text": "Acknowledge"}, "value": fingerprint},
                {"type": "button", "action_id": "opspilot_open_ticket", "text": {
                    "type": "plain_text", "text": "Open ticket"}, "value": fingerprint},
                {"type": "button", "action_id": "opspilot_draft_change", "text": {
                    "type": "plain_text", "text": "Draft change"}, "value": fingerprint},
            ]},
        ],
    }


def teams_card(fingerprint: str, result: dict) -> AdaptiveCard:
    """Adaptive Card with server-side action identifiers, not approval shortcuts."""
    return AdaptiveCard(
        schema="http://adaptivecards.io/schemas/adaptive-card.json",
        version="1.5",
        body=[
            TextBlock(text="OpsPilot incident", size="Large", weight="Bolder"),
            TextBlock(text=_summary(result), wrap=True),
            TextBlock(text=f"Thread: {fingerprint}", is_subtle=True, wrap=True),
        ],
        actions=[
            ExecuteAction(title="Acknowledge", data={"action": "ack", "thread": fingerprint}),
            ExecuteAction(title="Open ticket", data={"action": "ticket", "thread": fingerprint}),
            ExecuteAction(title="Draft change", data={"action": "change", "thread": fingerprint}),
        ],
    )
