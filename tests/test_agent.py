from langgraph.checkpoint.memory import InMemorySaver

from opspilot.agent import DeterministicBackend, answer_followup, build_graph


class FixtureTools:
    def query_metrics(self, args):
        return {"source_id": f"prom:{args.metric}:{args.service}", "series": []}

    def query_logs(self, args):
        return {"source_id": "loki:unavailable", "available": False, "entries": []}

    def get_recent_deploys(self, args):
        return {"source_id": f"deploys:{args.service}", "deploys": []}

    def search_runbooks(self, args):
        return {
            "source_id": "knowledge:runbooks",
            "results": [
                {"source_id": "RB-01", "source_type": "runbook", "body": "Check payment errors"}
            ],
        }

    def get_similar_incidents(self, args):
        return {"source_id": "knowledge:incidents", "results": []}

    def get_backup_jobs(self, args):
        return {"source_id": "backup:jobs", "jobs": []}

    def draft_ticket_update(self, args):
        return {"action_id": "pending-local-1", "status": "pending"}


def test_full_graph_uses_fixtures_and_persists_thread_state() -> None:
    graph = build_graph(FixtureTools(), DeterministicBackend(), InMemorySaver())
    config = {"configurable": {"thread_id": "incident-fixture-1"}}
    result = graph.invoke(
        {
            "alert": {
                "fingerprint": "abc",
                "labels": {"alertname": "PaymentErrorsInjected", "service": "payments"},
            },
            "incident_id": "INC9003",
        },
        config=config,
    )
    assert [step["node"] for step in result["trace"]] == [
        "triage",
        "gather_context",
        "hypothesise",
        "recommend",
        "draft_outputs",
    ]
    assert "RB-01" in result["summary"]
    assert "[source:alert:abc]" in result["summary"]
    assert result["pending_action_id"] == "pending-local-1"
    assert graph.get_state(config).values["summary"] == result["summary"]
    followup = answer_followup(result, "Why payment?", DeterministicBackend())
    assert "[source:alert:abc]" in followup["answer"]
    graph.update_state(config, {"followups": [followup]})
    assert graph.get_state(config).values["followups"][0]["question"] == "Why payment?"


def test_invalid_model_citations_retry_once_then_fail_closed() -> None:
    class InvalidBackend:
        calls = 0

        def hypothesize(self, alert, context):
            self.calls += 1
            return [{"text": "Unverified external claim", "source_ids": ["made-up-id"]}]

        def recommend(self, alert, context, hypotheses):
            return [{"text": "Use the runbook", "source_ids": ["RB-01"]}]

    backend = InvalidBackend()
    graph = build_graph(FixtureTools(), backend)
    result = graph.invoke(
        {
            "alert": {
                "fingerprint": "abc",
                "labels": {"alertname": "PaymentErrorsInjected", "service": "payments"},
            }
        }
    )
    assert backend.calls == 2
    assert "Hypothesis unavailable" in result["summary"]
    assert "made-up-id" not in result["summary"]
