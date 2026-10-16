from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from opspilot.storage import BackupJob, DeployEvent, Incident, PendingAction
from opspilot.tools import (
    BackupQuery,
    InvestigationTools,
    LogQuery,
    MetricQuery,
    SearchQuery,
    ServiceWindow,
    TicketDraft,
)


def test_metric_and_log_queries_use_validated_templates() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "prometheus" in request.url.host:
            return httpx.Response(200, json={"status": "success", "data": {"result": []}})
        return httpx.Response(200, json={"data": {"result": []}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools = InvestigationTools(
        prometheus_url="http://prometheus:9090", loki_url="http://loki:3100", client=client
    )
    metric = tools.query_metrics(MetricQuery(metric="error_rate", service="payments"))
    logs = tools.query_logs(LogQuery(kind="errors", service="payments"))
    assert metric["source_id"] == "prom:error_rate:payments"
    assert 'service="payments"' in metric["query"]
    assert logs["source_id"] == "loki:errors:payments"
    assert len(requests) == 2
    with pytest.raises(ValidationError):
        MetricQuery(metric="error_rate; DROP TABLE incidents", service="payments")
    with pytest.raises(ValidationError):
        LogQuery(kind="errors", service='payments"} |= "secret')


def test_database_read_tools_and_draft_are_local_and_typed() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in (
        BackupJob.__table__,
        DeployEvent.__table__,
        Incident.__table__,
        PendingAction.__table__,
    ):
        table.create(engine)
    with Session(engine) as session:
        session.add(
            BackupJob(
                policy="daily",
                client="backups",
                status="failed",
                bytes_written=0,
                error_code="NO_SPACE",
            )
        )
        session.add(
            DeployEvent(
                service="payments",
                version="v2",
                status="failed",
                occurred_at=datetime.now(UTC),
                source_id="deploy-2",
            )
        )
        session.add(
            Incident(
                number="INC9003",
                short_description="Payment errors",
                cmdb_ci="payments",
                state="new",
                work_notes="",
                close_notes="",
            )
        )
        session.commit()
    tools = InvestigationTools()
    tools.Session = sessionmaker(engine)
    assert (
        tools.get_backup_jobs(BackupQuery(status="failed"))["jobs"][0]["error_code"] == "NO_SPACE"
    )
    assert (
        tools.get_recent_deploys(ServiceWindow(service="payments"))["deploys"][0]["source_id"]
        == "deploy-2"
    )
    result = tools.draft_ticket_update(TicketDraft(incident_id="INC9003", text="Check RB-01 first"))
    assert result["status"] == "pending"
    with Session(engine) as session:
        assert session.get(Incident, "INC9003").work_notes == ""
        assert session.get(PendingAction, result["action_id"]).status == "pending"


def test_knowledge_tools_return_source_ids(monkeypatch) -> None:
    tools = InvestigationTools()
    monkeypatch.setattr(
        "opspilot.tools.search",
        lambda session, query, mode, limit, source_type: [
            {"source_id": "RB-01", "source_type": "runbook", "body": "steps"},
        ],
    )

    # The runbook tool asks retrieval to filter the candidate corpus first.
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    tools.Session = FakeSession
    result = tools.search_runbooks(SearchQuery(query="payment errors"))
    assert [row["source_id"] for row in result["results"]] == ["RB-01"]
