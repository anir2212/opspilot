"""PostgreSQL schema for the local ITSM, backup and knowledge services."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"

    number: Mapped[str] = mapped_column(String(24), primary_key=True)
    short_description: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(40), default="new")
    cmdb_ci: Mapped[str] = mapped_column(String(80))
    work_notes: Mapped[str] = mapped_column(Text, default="")
    close_notes: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    number: Mapped[str] = mapped_column(String(24), primary_key=True)
    short_description: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(40), default="draft")
    cmdb_ci: Mapped[str] = mapped_column(String(80))
    work_notes: Mapped[str] = mapped_column(Text, default="")
    close_notes: Mapped[str] = mapped_column(Text, default="")


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy: Mapped[str] = mapped_column(String(100))
    client: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    bytes_written: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DeployEvent(Base):
    __tablename__ = "deploy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[str] = mapped_column(String(80))


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ChangeReview(Base):
    __tablename__ = "change_reviews"

    change_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    request: Mapped[dict] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(20))
    reasons: Mapped[list] = mapped_column(JSON)
    checklist: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ChatThread(Base):
    __tablename__ = "chat_threads"

    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(100))
    message_id: Mapped[str] = mapped_column(String(100))
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertEvent(Base):
    __tablename__ = "alert_events"

    fingerprint: Mapped[str] = mapped_column(String(128), primary_key=True)
    alertname: Mapped[str] = mapped_column(String(100))
    service: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    labels: Mapped[dict] = mapped_column(JSON)
    annotations: Mapped[dict] = mapped_column(JSON)
    starts_at: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ends_at: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_count: Mapped[int] = mapped_column(Integer, default=1)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(50), index=True)
    source_type: Mapped[str] = mapped_column(String(20))
    heading: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(384), nullable=True)


def make_engine(url: str | None = None):
    return create_engine(
        url or os.getenv("OPSPILOT_DATABASE_URL")
        or "postgresql+psycopg://opspilot:opspilot@localhost:15432/opspilot",
        pool_pre_ping=True,
    )


def init_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_search "
                "ON knowledge_chunks USING gin "
                "(to_tsvector('english', heading || ' ' || body))"
            )
        )


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
