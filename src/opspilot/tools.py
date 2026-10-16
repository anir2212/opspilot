"""Validated incident investigation tools; read-only except pending-action drafts."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select

from opspilot.actions import create_pending
from opspilot.retrieval import encode, search
from opspilot.storage import (
    BackupJob,
    DeployEvent,
    Incident,
    KnowledgeChunk,
    make_engine,
    session_factory,
)

ServiceName = Literal["gateway", "payments", "backups"]


class MetricQuery(BaseModel):
    metric: Literal["error_rate", "p95_latency", "cpu_usage", "memory_bytes", "fault_active"]
    service: ServiceName
    range_minutes: int = Field(default=10, ge=1, le=60)


class LogQuery(BaseModel):
    kind: Literal["errors", "timeouts", "deploys"]
    service: ServiceName
    range_minutes: int = Field(default=10, ge=1, le=60)


class ServiceWindow(BaseModel):
    service: ServiceName
    window_minutes: int = Field(default=30, ge=1, le=1440)


class SearchQuery(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    k: int = Field(default=5, ge=1, le=10)


class BackupQuery(BaseModel):
    status: Literal["success", "failed", "partial", "running"] | None = None
    window_minutes: int = Field(default=60, ge=1, le=1440)


class TicketDraft(BaseModel):
    incident_id: str = Field(pattern=r"^INC\d{4,}$")
    text: str = Field(min_length=10, max_length=4000)


PROMQL_TEMPLATES = {
    "error_rate": (
        'sum(rate(opspilot_requests_total{{service="{service}",outcome="error"}}[1m])) '
        '/ clamp_min(sum(rate(opspilot_requests_total{{service="{service}"}}[1m])), 0.001)'
    ),
    "p95_latency": (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(opspilot_request_duration_seconds_bucket{{service="{service}"}}[1m])))'
    ),
    "cpu_usage": (
        'rate(process_cpu_seconds_total{{job="opspilot-lite",instance="{service}:8000"}}[1m])'
    ),
    "memory_bytes": (
        'process_resident_memory_bytes{{job="opspilot-lite",instance="{service}:8000"}}'
    ),
    "fault_active": 'opspilot_fault_active{{service="{service}"}}',
}

LOG_FILTERS = {"errors": "error", "timeouts": "timeout", "deploys": "deploy"}


class InvestigationTools:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.Session = session_factory(make_engine(database_url))
        self.prometheus_url = (
            prometheus_url or os.getenv("OPSPILOT_PROMETHEUS_URL") or "http://127.0.0.1:18109"
        )
        self.loki_url = loki_url or os.getenv("OPSPILOT_LOKI_URL", "")
        self.client = client or httpx.Client(timeout=8)

    def query_metrics(self, args: MetricQuery) -> dict:
        expression = PROMQL_TEMPLATES[args.metric].format(service=args.service)
        end = datetime.now(UTC)
        start = end - timedelta(minutes=args.range_minutes)
        response = self.client.get(
            self.prometheus_url + "/api/v1/query_range",
            params={
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": 15,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus query failed")
        return {
            "source_id": f"prom:{args.metric}:{args.service}",
            "query": expression,
            "series": payload.get("data", {}).get("result", []),
        }

    def query_logs(self, args: LogQuery) -> dict:
        if not self.loki_url:
            return {"source_id": "loki:unavailable", "available": False, "entries": []}
        # Enum validation makes interpolated label and filter values an allow-list.
        expression = f'{{service_name="{args.service}"}} |= "{LOG_FILTERS[args.kind]}"'
        now = datetime.now(UTC)
        response = self.client.get(
            self.loki_url + "/loki/api/v1/query_range",
            params={
                "query": expression,
                "start": int((now - timedelta(minutes=args.range_minutes)).timestamp() * 1e9),
                "end": int(now.timestamp() * 1e9),
                "limit": 100,
            },
        )
        response.raise_for_status()
        return {
            "source_id": f"loki:{args.kind}:{args.service}",
            "available": True,
            "entries": response.json().get("data", {}).get("result", []),
        }

    def get_recent_deploys(self, args: ServiceWindow) -> dict:
        cutoff = datetime.now(UTC) - timedelta(minutes=args.window_minutes)
        with self.Session() as session:
            rows = session.scalars(
                select(DeployEvent)
                .where(DeployEvent.service == args.service, DeployEvent.occurred_at >= cutoff)
                .order_by(DeployEvent.occurred_at.desc())
            ).all()
            return {
                "source_id": f"deploys:{args.service}",
                "deploys": [
                    {
                        "service": row.service,
                        "version": row.version,
                        "status": row.status,
                        "occurred_at": row.occurred_at.isoformat(),
                        "source_id": row.source_id,
                    }
                    for row in rows
                ],
            }

    def search_runbooks(self, args: SearchQuery) -> dict:
        with self.Session() as session:
            results = search(session, args.query, "hybrid", args.k, source_type="runbook")
        return {"source_id": "knowledge:runbooks", "results": results}

    def get_similar_incidents(self, args: SearchQuery) -> dict:
        vector = encode([args.query], query=True)[0]
        with self.Session() as session:
            chunks = session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.source_type == "incident")
                .order_by(KnowledgeChunk.embedding.cosine_distance(vector))
                .limit(args.k)
            ).all()
            return {
                "source_id": "knowledge:incidents",
                "results": [
                    {"number": row.source_id, "heading": row.heading, "body": row.body}
                    for row in chunks
                ],
            }

    def get_backup_jobs(self, args: BackupQuery) -> dict:
        cutoff = datetime.now(UTC) - timedelta(minutes=args.window_minutes)
        with self.Session() as session:
            query = select(BackupJob).where(BackupJob.started_at >= cutoff)
            if args.status:
                query = query.where(BackupJob.status == args.status)
            rows = session.scalars(query.order_by(BackupJob.started_at.desc())).all()
            return {
                "source_id": "backup:jobs",
                "jobs": [
                    {
                        "id": row.id,
                        "policy": row.policy,
                        "client": row.client,
                        "status": row.status,
                        "error_code": row.error_code,
                        "started_at": row.started_at.isoformat(),
                    }
                    for row in rows
                ],
            }

    def draft_ticket_update(self, args: TicketDraft) -> dict:
        with self.Session() as session:
            if session.get(Incident, args.incident_id) is None:
                raise ValueError("Incident does not exist")
            action = create_pending(session, "ticket_update", args.incident_id, {"text": args.text})
            return {
                "action_id": action.id,
                "status": action.status,
                "source_id": f"pending:{action.id}",
            }
