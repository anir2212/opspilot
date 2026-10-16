"""OpsPilot API entry point for persisted alerts, investigations and review."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.postgres import PostgresSaver
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.actions import router as actions_router
from opspilot.agent import DeterministicBackend, OllamaBackend, answer_followup, build_graph
from opspilot.change_approval import router as change_review_router
from opspilot.mocks import get_session
from opspilot.mocks import router as mocks_router
from opspilot.retrieval import search
from opspilot.storage import (
    AlertEvent,
    BackupJob,
    DeployEvent,
    init_database,
    make_engine,
    session_factory,
)
from opspilot.telemetry import AGENT_DURATION, AGENT_RUNS, configure_telemetry
from opspilot.tools import InvestigationTools

logger = logging.getLogger(__name__)


class IncomingAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None
    fingerprint: str | None = None


class AlertNotification(BaseModel):
    status: Literal["firing", "resolved"]
    receiver: str | None = None
    alerts: list[IncomingAlert]


class TriageRequest(BaseModel):
    alert: IncomingAlert
    incident_id: str | None = None
    thread_id: str | None = Field(default=None, max_length=255)


class FollowupRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def alert_thread_id(alert: IncomingAlert) -> str:
    if alert.fingerprint:
        return alert.fingerprint
    labels = json.dumps(alert.labels, sort_keys=True)
    return hashlib.sha256(labels.encode()).hexdigest()[:24]


def invoke_graph(graph, state: dict, thread_id: str) -> dict:
    started = time.perf_counter()
    try:
        result = graph.invoke(state, {"configurable": {"thread_id": thread_id}})
    except Exception:
        AGENT_RUNS.labels("error").inc()
        raise
    else:
        AGENT_RUNS.labels("ok").inc()
    finally:
        AGENT_DURATION.observe(time.perf_counter() - started)
    return result


def triage_and_deliver(graph, alert: IncomingAlert) -> None:
    fingerprint = alert_thread_id(alert)
    result = invoke_graph(graph, {"alert": alert.model_dump()}, fingerprint)
    if os.getenv("OPSPILOT_CHAT_DELIVERY") == "1":
        try:
            from opspilot.bots.slack_app import publish_incident

            publish_incident(fingerprint, result)
        except Exception:
            logger.exception("Slack delivery failed for incident %s", fingerprint)
        teams_url = os.getenv("OPSPILOT_TEAMS_BOT_URL", "")
        teams_token = os.getenv("OPSPILOT_TEAMS_INTERNAL_TOKEN", "")
        if teams_url and teams_token:
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        teams_url.rstrip("/") + "/internal/deliver",
                        json={"fingerprint": fingerprint, "result": result},
                        headers={"X-Delivery-Token": teams_token},
                    )
                    response.raise_for_status()
            except Exception:
                logger.exception("Teams delivery failed for incident %s", fingerprint)


def create_app(*, init_storage: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if init_storage:
            engine = make_engine()
            init_database(engine)
            app.state.Session = session_factory(engine)
            database_url = os.getenv(
                "OPSPILOT_DATABASE_URL",
                "postgresql+psycopg://opspilot:opspilot@localhost:15432/opspilot",
            )
            saver_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            with PostgresSaver.from_conn_string(saver_url) as saver:
                saver.setup()
                backend = (
                    OllamaBackend()
                    if os.getenv("OPSPILOT_LLM_BACKEND") == "ollama"
                    else DeterministicBackend()
                )
                app.state.backend = backend
                app.state.graph = build_graph(InvestigationTools(), backend, saver)
                yield
        else:
            yield

    app = FastAPI(title="OpsPilot API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:18117", "http://localhost:18117"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Approval-Token", "X-Approver"],
    )
    app.include_router(mocks_router)
    app.include_router(actions_router)
    app.include_router(change_review_router)
    notifications: list[dict[str, object]] = []
    lock = Lock()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/alerts", status_code=202)
    def receive_alerts(
        payload: AlertNotification, request: Request, background_tasks: BackgroundTasks
    ) -> dict[str, int]:
        # Keep raw deliveries for local debugging; persisted fingerprints deduplicate triage.
        with lock:
            notifications.append(payload.model_dump())
        triage_ids: set[str] = set()
        if hasattr(request.app.state, "Session"):
            with request.app.state.Session() as session:
                for alert in payload.alerts:
                    fingerprint = alert_thread_id(alert)
                    row = session.get(AlertEvent, fingerprint)
                    if alert.status == "firing" and (row is None or row.status != "firing"):
                        triage_ids.add(fingerprint)
                    if row is None:
                        row = AlertEvent(fingerprint=fingerprint, delivery_count=0)
                        session.add(row)
                    row.alertname = alert.labels.get("alertname", "unknown")
                    row.service = alert.labels.get("service", "unknown")
                    row.status = alert.status
                    row.labels = alert.labels
                    row.annotations = alert.annotations
                    row.starts_at = alert.startsAt
                    row.ends_at = alert.endsAt
                    row.last_received_at = datetime.now(UTC)
                    row.delivery_count += 1
                session.commit()
        graph = getattr(request.app.state, "graph", None)
        if graph is not None:
            for alert in payload.alerts:
                if alert_thread_id(alert) in triage_ids:
                    background_tasks.add_task(triage_and_deliver, graph, alert)
        return {"accepted": len(payload.alerts)}

    @app.get("/api/alerts")
    def list_alerts() -> list[dict[str, object]]:
        with lock:
            return list(notifications)

    @app.get("/api/incidents")
    def list_incident_alerts(request: Request, limit: int = Query(default=50, ge=1, le=200)):
        if not hasattr(request.app.state, "Session"):
            return {"incidents": []}
        with request.app.state.Session() as session:
            rows = session.scalars(
                select(AlertEvent).order_by(AlertEvent.last_received_at.desc()).limit(limit)
            ).all()
            return {
                "incidents": [
                    {
                        "fingerprint": row.fingerprint,
                        "alertname": row.alertname,
                        "service": row.service,
                        "status": row.status,
                        "starts_at": row.starts_at,
                        "last_received_at": row.last_received_at,
                        "delivery_count": row.delivery_count,
                    }
                    for row in rows
                ]
            }

    @app.get("/api/incidents/{fingerprint}/timeline")
    def incident_timeline(fingerprint: str, request: Request) -> dict:
        if not hasattr(request.app.state, "Session"):
            raise HTTPException(503, "Incident storage is not initialized")
        with request.app.state.Session() as session:
            alert = session.get(AlertEvent, fingerprint)
            if alert is None:
                raise HTTPException(404, "Incident not found")
            anchor = (
                datetime.fromisoformat(alert.starts_at.replace("Z", "+00:00"))
                if alert.starts_at else alert.last_received_at
            )
            events = [{
                "kind": "alert", "at": anchor.isoformat(),
                "title": f"{alert.alertname} {alert.status}",
                "source_id": f"alert:{fingerprint}",
            }]
            cutoff = anchor - timedelta(minutes=30)
            deploys = session.scalars(
                select(DeployEvent).where(DeployEvent.service == alert.service,
                                          DeployEvent.occurred_at >= cutoff,
                                          DeployEvent.occurred_at <= anchor + timedelta(minutes=10))
            ).all()
            events.extend({
                "kind": "deploy", "at": row.occurred_at.isoformat(),
                "title": f"{row.service} {row.version} ({row.status})",
                "source_id": row.source_id,
            } for row in deploys)
            if alert.service == "backups":
                jobs = session.scalars(
                    select(BackupJob).where(BackupJob.started_at >= cutoff,
                                            BackupJob.started_at <= anchor + timedelta(minutes=10))
                ).all()
                events.extend({
                    "kind": "backup", "at": row.started_at.isoformat(),
                    "title": f"{row.policy} on {row.client}: {row.status}",
                    "source_id": f"backup:{row.id}",
                } for row in jobs)
            events.sort(key=lambda event: event["at"])
            return {"fingerprint": fingerprint, "events": events}

    @app.get("/api/knowledge/search")
    def search_knowledge(
        q: str = Query(min_length=2),
        mode: Literal["lexical", "bm25", "vector", "hybrid", "rerank"] = "hybrid",
        limit: int = Query(default=5, ge=1, le=20),
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        return {"results": search(session, q, mode, limit)}

    @app.post("/api/triage")
    def triage_incident(payload: TriageRequest, request: Request) -> dict:
        graph = getattr(request.app.state, "graph", None)
        if graph is None:
            raise HTTPException(503, "Triage graph is not initialized")
        thread_id = payload.thread_id or alert_thread_id(payload.alert)
        result = invoke_graph(
            graph,
            {"alert": payload.alert.model_dump(), "incident_id": payload.incident_id},
            thread_id,
        )
        return {
            "thread_id": thread_id,
            "summary": result["summary"],
            "hypotheses": result["hypotheses"],
            "recommendations": result["recommendations"],
            "trace": result["trace"],
            "pending_action_id": result.get("pending_action_id"),
        }

    @app.get("/api/triage/{thread_id}")
    def get_triage(thread_id: str, request: Request) -> dict:
        graph = getattr(request.app.state, "graph", None)
        if graph is None:
            raise HTTPException(503, "Triage graph is not initialized")
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        if not snapshot.values:
            raise HTTPException(404, "Thread not found")
        return {"thread_id": thread_id, "state": snapshot.values}

    @app.post("/api/triage/{thread_id}/followup")
    def triage_followup(thread_id: str, payload: FollowupRequest, request: Request) -> dict:
        graph = getattr(request.app.state, "graph", None)
        if graph is None:
            raise HTTPException(503, "Triage graph is not initialized")
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise HTTPException(404, "Thread not found")
        reply = answer_followup(snapshot.values, payload.question, request.app.state.backend)
        history = snapshot.values.get("followups", []) + [reply]
        graph.update_state(config, {"followups": history})
        return {"thread_id": thread_id, **reply}

    configure_telemetry(app, "opspilot-api")
    return app


app = create_app(init_storage=True)
