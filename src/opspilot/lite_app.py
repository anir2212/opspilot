"""Three lightweight demo services with controlled, labeled faults.

The fault gauges are synthetic. They are deliberately separate from actual CPU,
memory, and disk telemetry so a demo never exhausts a developer's laptop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from threading import Lock

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
)
from prometheus_client.exposition import generate_latest
from pydantic import BaseModel, Field

from opspilot.faults import FAULT_SERVICE
from opspilot.telemetry import configure_telemetry

logger = logging.getLogger(__name__)


class ChaosRequest(BaseModel):
    name: str
    duration_seconds: int = Field(ge=1, le=3600)


@dataclass
class FaultState:
    active_until: dict[str, float] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def set(self, name: str, duration_seconds: int) -> None:
        with self.lock:
            self.active_until[name] = time.monotonic() + duration_seconds

    def active(self, name: str) -> bool:
        with self.lock:
            until = self.active_until.get(name, 0.0)
            if until <= time.monotonic():
                self.active_until.pop(name, None)
                return False
            return True

    def clear(self) -> None:
        with self.lock:
            self.active_until.clear()


def create_app(service: str, payment_url: str | None = None) -> FastAPI:
    if service not in {"gateway", "payments", "backups"}:
        raise ValueError(f"unknown demo service: {service}")

    app = FastAPI(title=f"OpsPilot {service} demo", version="0.1.0")
    state = FaultState()
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    requests = Counter(
        "opspilot_requests_total", "Requests by outcome", ["service", "outcome"], registry=registry
    )
    latency = Histogram(
        "opspilot_request_duration_seconds", "Demo request duration", ["service"], registry=registry
    )
    fault_metric = Gauge(
        "opspilot_fault_active",
        "Active injected fault (synthetic)",
        ["service", "kind"],
        registry=registry,
    )
    deploy_metric = Gauge("opspilot_deploy_bad", "Bad deploy flag", ["service"], registry=registry)
    app.state.faults = state

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service, "status": "ok"}

    @app.post("/chaos")
    def inject(body: ChaosRequest) -> dict[str, object]:
        if FAULT_SERVICE.get(body.name) != service:
            raise HTTPException(400, f"{body.name} is not a fault for {service}")
        state.set(body.name, body.duration_seconds)
        fault_metric.labels(service, body.name).set(1)
        if body.name == "bad_deploy":
            deploy_metric.labels(service).set(1)
        logger.info("fault injected service=%s name=%s", service, body.name)
        return {"service": service, "fault": body.name, "duration_seconds": body.duration_seconds}

    @app.delete("/chaos")
    def clear() -> dict[str, str]:
        state.clear()
        for name, owner in FAULT_SERVICE.items():
            if owner == service:
                fault_metric.labels(service, name).set(0)
        deploy_metric.labels(service).set(0)
        logger.info("faults cleared service=%s", service)
        return {"status": "cleared"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> PlainTextResponse:
        for name, owner in FAULT_SERVICE.items():
            if owner == service:
                fault_metric.labels(service, name).set(int(state.active(name)))
        deploy_metric.labels(service).set(int(state.active("bad_deploy")))
        return PlainTextResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/work")
    async def work() -> dict[str, object]:
        started = time.monotonic()
        try:
            if state.active("latency"):
                await asyncio.sleep(2.4)
            if state.active("cpu_saturation"):
                # Expose a simulated fault gauge without burning CPU on the host.
                await asyncio.sleep(0.2)
            if state.active("memory_leak") or state.active("disk_full"):
                raise HTTPException(507, "simulated resource exhaustion")
            if service == "payments" and any(
                state.active(name) for name in ("payment_errors", "bad_deploy", "dependency_down")
            ):
                raise HTTPException(503, "simulated payment dependency failure")
            if service == "backups" and state.active("backup_failure"):
                raise HTTPException(503, "simulated backup job failure")
            if service == "gateway" and payment_url:
                async with httpx.AsyncClient(timeout=4) as client:
                    response = await client.get(f"{payment_url}/work")
                if response.status_code >= 400:
                    raise HTTPException(502, "payment dependency returned an error")
            requests.labels(service, "ok").inc()
            return {"service": service, "status": "ok"}
        except HTTPException:
            requests.labels(service, "error").inc()
            logger.warning("error work request failed service=%s", service)
            raise
        finally:
            latency.labels(service).observe(time.monotonic() - started)

    configure_telemetry(app, service)
    return app


app = create_app(
    os.getenv("OPSPILOT_SERVICE_NAME", "gateway"),
    os.getenv("OPSPILOT_PAYMENT_URL"),
)
