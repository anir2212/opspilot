"""Conservative, auditable deployment change review and sign-off tracking."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.actions import require_approval_token
from opspilot.mocks import get_session
from opspilot.storage import AlertEvent, ChangeReview, DeployEvent

router = APIRouter(prefix="/api/change-reviews", tags=["change reviews"])
REQUIRED_SIGNOFFS = frozenset({"qa", "owner", "security"})


class Decision(StrEnum):
    APPROVE = "APPROVE"
    NEEDS_INFO = "NEEDS INFO"
    REJECT = "REJECT"


class DeploymentRequest(BaseModel):
    change_id: str = Field(pattern=r"^CHG\d{4,}$")
    service: str = Field(pattern=r"^(gateway|payments|backups)$")
    version: str = Field(min_length=1, max_length=80)
    window_start: datetime
    window_end: datetime
    test_evidence: list[str] = Field(default_factory=list, max_length=20)
    signoffs: dict[str, str] = Field(default_factory=dict)
    description: str = Field(min_length=5, max_length=500)

    @field_validator("window_start", "window_end")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Deployment windows must include a timezone")
        return value


class ReviewContext(BaseModel):
    frozen: bool = False
    calendar_verified: bool = False
    open_severe_incidents: list[str] = Field(default_factory=list)
    recent_failed_deploys: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    decision: Decision
    reasons: list[str]
    checklist: dict[str, bool]


def evaluate_change(request: DeploymentRequest, context: ReviewContext) -> ReviewResult:
    """Evaluate evidence in priority order; a hard blocker outranks missing evidence."""
    signoffs = {role for role, signer in request.signoffs.items() if signer.strip()}
    checklist = {
        "valid_window": request.window_start < request.window_end,
        "test_evidence": bool(request.test_evidence),
        "qa_signoff": "qa" in signoffs,
        "owner_signoff": "owner" in signoffs,
        "security_signoff": "security" in signoffs,
        "outside_freeze": not context.frozen,
        "calendar_verified": context.calendar_verified,
        "no_open_severe_incidents": not context.open_severe_incidents,
        "no_recent_failed_deploy": not context.recent_failed_deploys,
    }
    hard_blockers = []
    if not checklist["valid_window"]:
        hard_blockers.append("The deployment window must end after it starts.")
    if context.frozen:
        hard_blockers.append("The change window overlaps a deployment freeze.")
    if context.open_severe_incidents:
        hard_blockers.append("Open SEV1/2 incident: " + ", ".join(context.open_severe_incidents))
    if context.recent_failed_deploys:
        hard_blockers.append("Recent failed deploy: " + ", ".join(context.recent_failed_deploys))
    if hard_blockers:
        return ReviewResult(decision=Decision.REJECT, reasons=hard_blockers, checklist=checklist)
    missing = []
    if not request.test_evidence:
        missing.append("Provide test evidence.")
    if not context.calendar_verified:
        missing.append("Verify the deployment freeze calendar.")
    for role in sorted(REQUIRED_SIGNOFFS - signoffs):
        missing.append(f"Obtain {role} sign-off.")
    if missing:
        return ReviewResult(decision=Decision.NEEDS_INFO, reasons=missing, checklist=checklist)
    return ReviewResult(
        decision=Decision.APPROVE,
        reasons=["All configured policy checks passed; execution still requires a human."],
        checklist=checklist,
    )


def context_from_database(session: Session, request: DeploymentRequest) -> ReviewContext:
    """Build local evidence. A missing freeze calendar fails closed at the API boundary."""
    recent = datetime.now(UTC) - timedelta(days=7)
    alerts = session.scalars(
        select(AlertEvent).where(
            AlertEvent.service == request.service, AlertEvent.status == "firing"
        )
    ).all()
    severe = [
        row.fingerprint
        for row in alerts
        if row.labels.get("severity", "").lower() in {"sev1", "sev2", "critical"}
    ]
    failed = session.scalars(
        select(DeployEvent).where(
            DeployEvent.service == request.service,
            DeployEvent.status == "failed",
            DeployEvent.occurred_at >= recent,
        )
    ).all()
    calendar_path = Path(os.getenv("OPSPILOT_FREEZE_CALENDAR", "infra/change_freezes.json"))
    calendar_verified = calendar_path.is_file()
    frozen = False
    if calendar_verified:
        for item in json.loads(calendar_path.read_text())["windows"]:
            start = datetime.fromisoformat(item["start"])
            end = datetime.fromisoformat(item["end"])
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("Freeze calendar intervals must include a timezone")
            if start < request.window_end and end > request.window_start:
                frozen = True
    return ReviewContext(
        frozen=frozen, calendar_verified=calendar_verified,
        open_severe_incidents=severe, recent_failed_deploys=[r.source_id for r in failed],
    )


@router.post("", status_code=201)
def create_review(request: DeploymentRequest, session: Session = Depends(get_session)) -> dict:
    if session.get(ChangeReview, request.change_id):
        raise HTTPException(409, "Review already exists")
    result = evaluate_change(request, context_from_database(session, request))
    row = ChangeReview(
        change_id=request.change_id,
        request=request.model_dump(mode="json"),
        decision=result.decision.value,
        reasons=result.reasons,
        checklist=result.checklist,
    )
    session.add(row)
    session.commit()
    return {"change_id": row.change_id, "decision": row.decision, "reasons": row.reasons,
            "checklist": row.checklist}


@router.get("/{change_id}")
def get_review(change_id: str, session: Session = Depends(get_session)) -> dict:
    row = session.get(ChangeReview, change_id)
    if row is None:
        raise HTTPException(404, "Review not found")
    return {"change_id": row.change_id, "request": row.request, "decision": row.decision,
            "reasons": row.reasons, "checklist": row.checklist, "updated_at": row.updated_at}


class Signoff(BaseModel):
    role: str = Field(pattern=r"^(qa|owner|security)$")
    signer: str = Field(min_length=2, max_length=80)


@router.post("/{change_id}/signoffs")
def record_signoff(
    change_id: str,
    signoff: Signoff,
    session: Session = Depends(get_session),
    token: str | None = Header(default=None, alias="X-Approval-Token"),
) -> dict:
    require_approval_token(token)
    row = session.get(ChangeReview, change_id)
    if row is None:
        raise HTTPException(404, "Review not found")
    request = DeploymentRequest.model_validate(row.request)
    request.signoffs[signoff.role] = signoff.signer
    result = evaluate_change(request, context_from_database(session, request))
    row.request = request.model_dump(mode="json")
    row.decision = result.decision.value
    row.reasons = result.reasons
    row.checklist = result.checklist
    row.updated_at = datetime.now(UTC)
    session.commit()
    return {"change_id": row.change_id, "decision": row.decision, "reasons": row.reasons,
            "checklist": row.checklist}
