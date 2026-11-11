"""Evaluation selection and false-alarm scoring regressions."""

from pathlib import Path

import pytest

from eval import run_eval
from eval.run_eval import load_scenarios, score


def test_named_scenario_selection_is_exact_and_ordered() -> None:
    selected = load_scenarios(scenario_ids=[
        "payment_errors_false_alarm", "backup_failure_baseline",
    ])
    assert [case.id for case in selected] == [
        "payment_errors_false_alarm", "backup_failure_baseline",
    ]
    with pytest.raises(ValueError, match="Unknown scenario IDs"):
        load_scenarios(scenario_ids=["missing"])
    with pytest.raises(ValueError, match="must not be repeated"):
        load_scenarios(scenario_ids=["backup_failure_baseline"] * 2)


def test_false_alarm_requires_recommendations_to_abstain_too() -> None:
    scenario = load_scenarios(scenario_ids=["backup_failure_false_alarm"])[0]
    output = {
        "hypotheses": [],
        "recommendations": [{
            "text": "A backup failure is the cause",
            "cause_id": "backup_failure",
            "source_ids": [f"alert:{scenario.alert['fingerprint']}"],
        }],
        "context": {},
        "duration_seconds": 0.1,
    }
    assert score(scenario, output)["false_alarm_abstained"] is False
    output["recommendations"][0]["cause_id"] = None
    assert score(scenario, output)["false_alarm_abstained"] is True


def test_fixture_update_preserves_historical_model_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "RESULTS.md"
    destination.write_text(
        "# Results\n<!-- agent-eval:start -->\nold fixture\n"
        "<!-- agent-eval:end -->\n\n### Local LLM\nDated historical finding.\n"
    )
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    row = {
        "root_cause_accuracy_at_1": 0.0,
        "root_cause_accuracy_at_3": 0.0,
        "runbook_recall_at_3": 0.0,
        "false_alarm_abstention": 1.0,
        "citation_id_validity_not_precision": 1.0,
        "median_time_to_first_summary_seconds": 0.01,
    }
    run_eval.update_results({"single_shot_rag": row, "agent": row})
    text = destination.read_text()
    assert "old fixture" not in text
    assert text.count("<!-- agent-eval:end -->") == 1
    assert "### Local LLM\nDated historical finding." in text
