"""Local chat-button effects; only draft actions can later reach mock ITSM."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from opspilot.actions import create_pending
from opspilot.storage import AlertEvent, ChatThread


def acknowledge(session: Session, platform: str, fingerprint: str, actor: str) -> bool:
    row = session.get(ChatThread, (platform, fingerprint))
    if row is None:
        return False
    if row.acknowledged_by is None:
        row.acknowledged_by = actor[:100]
        row.acknowledged_at = datetime.now(UTC)
        session.commit()
    return True


def draft_from_alert(session: Session, fingerprint: str, kind: str) -> dict:
    alert = session.get(AlertEvent, fingerprint)
    if alert is None:
        raise ValueError("Unknown alert")
    if kind not in {"ticket", "change"}:
        raise ValueError("Unsupported draft kind")
    suffix = f"{uuid.uuid4().int % 10**12:012d}"
    target = ("INC" if kind == "ticket" else "CHG") + suffix
    payload = {
        "short_description": f"{alert.alertname} on {alert.service}",
        "service": alert.service,
        "work_notes": f"Alert fingerprint: {fingerprint}. Operator review required.",
    }
    action = create_pending(
        session, "ticket_create" if kind == "ticket" else "change_request", target, payload
    )
    return {"action_id": action.id, "target": target, "status": action.status}
