"""Two-phase approval for agent-proposed writes to the local mock ITSM."""

from __future__ import annotations

import hmac
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.mocks import get_session
from opspilot.storage import ChangeRequest, Incident, PendingAction

router = APIRouter()


def create_pending(session: Session, kind: str, target: str, payload: dict) -> PendingAction:
    if kind not in {"ticket_update", "ticket_create", "change_request"}:
        raise ValueError("Unsupported action kind")
    action = PendingAction(id=str(uuid.uuid4()), kind=kind, target=target, payload=payload)
    session.add(action)
    session.commit()
    return action


def action_dict(row: PendingAction) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def require_approval_token(token: str | None) -> None:
    expected = os.getenv("OPSPILOT_APPROVAL_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Approval token is not configured")
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Approval token is invalid")


@router.get("/api/pending-actions")
def list_pending(status: str = "pending", session: Session = Depends(get_session)):
    rows = session.scalars(
        select(PendingAction)
        .where(PendingAction.status == status)
        .order_by(PendingAction.created_at.desc())
    ).all()
    return {"actions": [action_dict(row) for row in rows]}


@router.post("/api/pending-actions/{action_id}/approve")
def approve(
    action_id: str,
    session: Session = Depends(get_session),
    token: str | None = Header(default=None, alias="X-Approval-Token"),
    approver: str = Header(default="local-operator", alias="X-Approver"),
):
    require_approval_token(token)
    row = session.get(PendingAction, action_id)
    if row is None:
        raise HTTPException(404, "Action not found")
    if row.status != "pending":
        raise HTTPException(409, f"Action is {row.status}")
    if row.kind == "ticket_update":
        incident = session.get(Incident, row.target)
        if incident is None:
            raise HTTPException(404, "Target incident not found")
        note = str(row.payload["text"])
        incident.work_notes = (incident.work_notes + "\n" + note).strip()
    elif row.kind == "ticket_create":
        if session.get(Incident, row.target):
            raise HTTPException(409, "Incident number already exists")
        session.add(
            Incident(
                number=row.target,
                short_description=str(row.payload["short_description"]),
                cmdb_ci=str(row.payload["service"]),
                state="new",
                work_notes=str(row.payload.get("work_notes", "")),
                close_notes="",
            )
        )
    elif row.kind == "change_request":
        if session.get(ChangeRequest, row.target):
            raise HTTPException(409, "Change number already exists")
        session.add(
            ChangeRequest(
                number=row.target,
                short_description=str(row.payload["short_description"]),
                cmdb_ci=str(row.payload["service"]),
                state="draft",
                work_notes=str(row.payload.get("work_notes", "")),
                close_notes="",
            )
        )
    row.status = "approved"
    row.decided_at = datetime.now(UTC)
    row.decided_by = approver[:80]
    session.commit()
    return {"action": action_dict(row)}


@router.post("/api/pending-actions/{action_id}/reject")
def reject(
    action_id: str,
    session: Session = Depends(get_session),
    token: str | None = Header(default=None, alias="X-Approval-Token"),
    approver: str = Header(default="local-operator", alias="X-Approver"),
):
    require_approval_token(token)
    row = session.get(PendingAction, action_id)
    if row is None:
        raise HTTPException(404, "Action not found")
    if row.status != "pending":
        raise HTTPException(409, f"Action is {row.status}")
    row.status = "rejected"
    row.decided_at = datetime.now(UTC)
    row.decided_by = approver[:80]
    session.commit()
    return {"action": action_dict(row)}
