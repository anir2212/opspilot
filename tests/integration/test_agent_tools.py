"""Live tool contracts against local Prometheus, PostgreSQL and the knowledge index."""

import os

import pytest

from opspilot.storage import Incident, PendingAction, make_engine, session_factory
from opspilot.tools import (
    BackupQuery,
    InvestigationTools,
    LogQuery,
    MetricQuery,
    SearchQuery,
    ServiceWindow,
    TicketDraft,
)


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("OPSPILOT_INTEGRATION") != "1", reason="requires Compose")
def test_all_seven_investigation_tools_with_local_sources() -> None:
    tools = InvestigationTools(loki_url="http://127.0.0.1:18131")
    assert tools.query_metrics(MetricQuery(metric="error_rate", service="payments"))["source_id"]
    assert tools.query_logs(LogQuery(kind="errors", service="payments"))["available"] is True
    assert "deploys" in tools.get_recent_deploys(ServiceWindow(service="payments"))
    assert tools.search_runbooks(SearchQuery(query="payment errors"))["results"]
    assert tools.get_similar_incidents(SearchQuery(query="payment errors"))["results"]
    assert "jobs" in tools.get_backup_jobs(BackupQuery())
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        before = session.get(Incident, "INC0001").work_notes
    action_id = None
    try:
        action = tools.draft_ticket_update(
            TicketDraft(incident_id="INC0001", text="Investigate payment metrics and RB-01")
        )
        action_id = action["action_id"]
        assert action["status"] == "pending"
        with Session() as session:
            assert session.get(Incident, "INC0001").work_notes == before
    finally:
        if action_id:
            with Session() as session:
                row = session.get(PendingAction, action_id)
                if row:
                    session.delete(row)
                    session.commit()
