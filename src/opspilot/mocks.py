"""Local ServiceNow-shaped and backup REST adapters with persistent data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.storage import BackupJob, ChangeRequest, Incident, make_engine, session_factory

router = APIRouter()
_session_factory = session_factory(make_engine())


def get_session():
    with _session_factory() as session:
        yield session


class IncidentPayload(BaseModel):
    number: str = Field(pattern=r"^INC\d{4,}$")
    short_description: str = Field(min_length=3, max_length=500)
    state: str = "new"
    cmdb_ci: str
    work_notes: str = ""
    close_notes: str = ""


class ChangePayload(BaseModel):
    number: str = Field(pattern=r"^CHG\d{4,}$")
    short_description: str = Field(min_length=3, max_length=500)
    state: str = "draft"
    cmdb_ci: str
    work_notes: str = ""
    close_notes: str = ""


class BackupPayload(BaseModel):
    policy: str
    client: str
    status: Literal["success", "failed", "partial", "running"]
    bytes_written: int = Field(ge=0)
    error_code: str | None = None


def _record(obj) -> dict:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


@router.get("/api/now/table/incident")
def list_incidents(cmdb_ci: str | None = None, session: Session = Depends(get_session)):
    query = select(Incident).order_by(Incident.opened_at.desc())
    if cmdb_ci:
        query = query.where(Incident.cmdb_ci == cmdb_ci)
    return {"result": [_record(row) for row in session.scalars(query).all()]}


@router.post("/api/now/table/incident", status_code=201)
def create_incident(payload: IncidentPayload, session: Session = Depends(get_session)):
    if session.get(Incident, payload.number):
        raise HTTPException(409, "Incident number already exists")
    row = Incident(**payload.model_dump())
    session.add(row)
    session.commit()
    return {"result": _record(row)}


@router.get("/api/now/table/change_request")
def list_changes(session: Session = Depends(get_session)):
    return {"result": [_record(row) for row in session.scalars(select(ChangeRequest)).all()]}


@router.post("/api/now/table/change_request", status_code=201)
def create_change(payload: ChangePayload, session: Session = Depends(get_session)):
    if session.get(ChangeRequest, payload.number):
        raise HTTPException(409, "Change number already exists")
    row = ChangeRequest(**payload.model_dump())
    session.add(row)
    session.commit()
    return {"result": _record(row)}


@router.get("/api/backups/jobs")
def list_backup_jobs(
    status: str | None = Query(default=None), session: Session = Depends(get_session)
):
    query = select(BackupJob).order_by(BackupJob.started_at.desc())
    if status:
        query = query.where(BackupJob.status == status)
    return {"jobs": [_record(row) for row in session.scalars(query).all()]}


@router.post("/api/backups/jobs", status_code=201)
def create_backup_job(payload: BackupPayload, session: Session = Depends(get_session)):
    row = BackupJob(**payload.model_dump(), started_at=datetime.now(UTC))
    session.add(row)
    session.commit()
    return {"job": _record(row)}
