"""Evaluate authored fixtures; a real model run is opt-in and separately labeled."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from statistics import median

import httpx
import yaml
from pydantic import BaseModel, Field

from opspilot.agent import (
    DeterministicBackend,
    GeminiBackend,
    OllamaBackend,
    _available_sources,
    build_graph,
)
from opspilot.citation_review import review_items, write_review_queue

ROOT = Path(__file__).resolve().parents[1]


class Scenario(BaseModel):
    id: str
    source: str
    variant: str
    alert: dict
    expected_faults: list[str] = Field(default_factory=list)
    expected_runbooks: list[str] = Field(default_factory=list)
    evidence: dict
    runbook_candidates: list[str]


class ScenarioTools:
    """Recorded context shape; deliberately separate from a live infrastructure run."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario

    def query_metrics(self, args):
        return {"source_id": f"fixture:metric:{args.metric}:{args.service}",
                "observation": self.scenario.evidence}

    def query_logs(self, args):
        return {"source_id": f"fixture:log:{args.service}", "available": True,
                "entries": [self.scenario.evidence["signal"]]}

    def query_anomalies(self, args):
        return {"source_id": f"fixture:anomaly:{args.service}", "available": False,
                "reason": "Fixture has no pre-incident Prometheus series"}

    def query_traces(self, args):
        return {"source_id": f"fixture:tempo:{args.service}", "available": False,
                "traces": []}

    def get_recent_deploys(self, args):
        if "bad_deploy" not in self.scenario.expected_faults:
            return {"source_id": f"fixture:deploys:{args.service}", "deploys": []}
        return {"source_id": f"fixture:deploys:{args.service}", "deploys": [
            {"source_id": f"fixture:deploy:{self.scenario.id}", "status": "failed"}
        ]}

    def search_runbooks(self, args):
        return {"source_id": "fixture:runbooks", "results": [
            {"source_id": identifier, "body": (
                ROOT / "knowledge" / "runbooks" / f"{identifier}.md"
            ).read_text()[:1800]}
            for identifier in self.scenario.runbook_candidates[:args.k]
        ]}

    def get_similar_incidents(self, args):
        return {"source_id": "fixture:incidents", "results": []}

    def get_backup_jobs(self, args):
        jobs = []
        if "backup_failure" in self.scenario.expected_faults:
            jobs = [{"id": 1, "status": "failed", "error_code": "SYNTHETIC"}]
        return {"source_id": "fixture:backups", "jobs": jobs}

    def draft_ticket_update(self, args):
        raise AssertionError("Evaluation must never draft a ticket")


def load_scenarios(limit: int | None = None,
                   scenario_ids: list[str] | None = None) -> list[Scenario]:
    paths = sorted((ROOT / "eval" / "scenarios").glob("*.yaml"))
    scenarios = [Scenario.model_validate(yaml.safe_load(path.read_text())) for path in paths]
    if len(scenarios) != 40:
        raise ValueError(f"Expected 40 committed fixtures, found {len(scenarios)}")
    if len({scenario.id for scenario in scenarios}) != 40:
        raise ValueError("Scenario IDs must be unique")
    if scenario_ids is not None:
        if limit is not None:
            raise ValueError("Select scenario IDs or a limit, not both")
        by_id = {scenario.id: scenario for scenario in scenarios}
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("Scenario IDs must not be repeated")
        unknown = set(scenario_ids) - by_id.keys()
        if unknown:
            raise ValueError(f"Unknown scenario IDs: {sorted(unknown)}")
        return [by_id[identifier] for identifier in scenario_ids]
    return scenarios[:limit] if limit is not None else scenarios


def score(scenario: Scenario, output: dict) -> dict:
    hypotheses = output.get("hypotheses", [])
    recommendations = output.get("recommendations", [])
    causes = [claim.get("cause_id") for claim in hypotheses if claim.get("cause_id")]
    recommendation_causes = [
        claim.get("cause_id") for claim in recommendations if claim.get("cause_id")
    ]
    cited_runbooks = {
        source for claim in recommendations for source in claim.get("source_ids", [])
        if source.startswith("RB-")
    }
    available = _available_sources(scenario.alert, output.get("context", {}))
    all_claims = hypotheses + recommendations
    cited = [source for claim in all_claims for source in claim.get("source_ids", [])]
    return {
        "id": scenario.id,
        "variant": scenario.variant,
        "root_at_1": bool(causes and causes[0] in scenario.expected_faults)
        if scenario.expected_faults else None,
        "root_at_3": bool(set(causes[:3]) & set(scenario.expected_faults))
        if scenario.expected_faults else None,
        "false_alarm_abstained": (
            not causes and not recommendation_causes and not cited_runbooks
        ) if not scenario.expected_faults else None,
        "runbook_recall_at_3": (
            len(cited_runbooks & set(scenario.expected_runbooks))
            / len(scenario.expected_runbooks)
            if scenario.expected_runbooks else None
        ),
        "citation_id_validity": (
            sum(source in available for source in cited) / len(cited) if cited else None
        ),
        "tool_calls": len(output.get("context", {})),
        "time_to_first_summary_seconds": output["duration_seconds"],
        "input_tokens": output.get("input_tokens"),
        "output_tokens": output.get("output_tokens"),
        "failed": bool(output.get("error")),
        "error_type": output.get("error"),
    }


def _error_type(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTPStatusError:{exc.response.status_code}"
    return type(exc).__name__


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if not row["failed"]]

    def average(key):
        values = [row[key] for row in completed if row[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "scenario_count": len(rows),
        "successful_scenarios": len(completed),
        "root_cause_accuracy_at_1": average("root_at_1"),
        "root_cause_accuracy_at_3": average("root_at_3"),
        "false_alarm_abstention": average("false_alarm_abstained"),
        "runbook_recall_at_3": average("runbook_recall_at_3"),
        "citation_id_validity_not_precision": average("citation_id_validity"),
        "citation_semantic_precision": None,
        "median_time_to_first_summary_seconds": (
            round(median(row["time_to_first_summary_seconds"] for row in completed), 4)
            if completed else None
        ),
        "mean_tool_calls_per_scenario": average("tool_calls"),
        "mean_input_tokens_per_scenario": average("input_tokens"),
        "mean_output_tokens_per_scenario": average("output_tokens"),
        "failed_scenarios": sum(row["failed"] for row in rows),
        "cost_usd_per_incident": None,
    }


def run(backend_name: str = "fixture", limit: int | None = None,
        collect_citations: bool = False, scenario_ids: list[str] | None = None,
        capture_outputs: bool = False) -> dict:
    scenarios = load_scenarios(limit, scenario_ids)
    backend = {
        "fixture": DeterministicBackend,
        "ollama": OllamaBackend,
        "gemini": GeminiBackend,
    }[backend_name]()
    model_digest = None
    if backend_name == "ollama":
        # Never pull: an absent local model is a blocker, not something to download here.
        model_digest = backend.model_digest()
        if model_digest is None:
            raise RuntimeError(f"Ollama model {backend.model!r} is not available locally")
    provenance = {"backend": backend_name, "model": getattr(backend, "model", None),
                  "model_digest": model_digest}
    agent_rows = []
    rag_rows = []
    citation_review_items = []
    outputs = {}
    for scenario in scenarios:
        tools = ScenarioTools(scenario)
        graph = build_graph(tools, backend)
        before_in = getattr(backend, "input_tokens", None)
        before_out = getattr(backend, "output_tokens", None)
        started = time.perf_counter()
        try:
            agent = graph.invoke({"alert": scenario.alert})
        except Exception as exc:
            agent = {"hypotheses": [], "recommendations": [], "context": {},
                     "error": _error_type(exc)}
        agent["duration_seconds"] = time.perf_counter() - started
        if before_in is not None:
            agent["input_tokens"] = backend.input_tokens - before_in
            agent["output_tokens"] = backend.output_tokens - before_out
        agent_rows.append(score(scenario, agent))
        if collect_citations:
            citation_review_items.extend(
                review_items(scenario.id, "agent", scenario.alert, agent, provenance)
            )

        # One model call and runbook context only: an intentionally smaller RAG baseline.
        started = time.perf_counter()
        context = {"runbooks": tools.search_runbooks(type("Q", (), {"k": 3})())}
        before_in = getattr(backend, "input_tokens", None)
        before_out = getattr(backend, "output_tokens", None)
        error = None
        try:
            claims = backend.hypothesize(scenario.alert, context)
        except Exception as exc:
            claims = []
            error = _error_type(exc)
        rag = {
            "hypotheses": claims, "recommendations": claims,
            "context": context, "duration_seconds": time.perf_counter() - started,
            "error": error,
        }
        if before_in is not None:
            rag["input_tokens"] = backend.input_tokens - before_in
            rag["output_tokens"] = backend.output_tokens - before_out
        rag_rows.append(score(scenario, rag))
        if capture_outputs:
            outputs[scenario.id] = {
                "agent": {key: agent.get(key) for key in (
                    "hypotheses", "recommendations", "context", "error")},
                "single_shot_rag": {key: rag.get(key) for key in (
                    "hypotheses", "recommendations", "context", "error")},
            }
        if collect_citations:
            citation_review_items.extend(
                review_items(scenario.id, "single_shot_rag", scenario.alert, rag, provenance)
            )
    report = {
        "backend": backend_name,
        "model": getattr(backend, "model", None),
        "model_digest": model_digest,
        "scope": "authored context fixtures; no live fault injection in this harness run",
        "false_alarm_rule": (
            "no cause_id in hypotheses or recommendations and no runbook citation "
            "in recommendations"
        ),
        "single_shot_rag": summarize(rag_rows),
        "agent": summarize(agent_rows),
        "rows": {"single_shot_rag": rag_rows, "agent": agent_rows},
    }
    if collect_citations:
        report["citation_review_items"] = citation_review_items
    if capture_outputs:
        report["outputs"] = outputs
    return report


def update_results(report: dict) -> None:
    start_marker = "<!-- agent-eval:start -->"
    end_marker = "<!-- agent-eval:end -->"
    destination = ROOT / "RESULTS.md"
    text = destination.read_text()
    if start_marker not in text or end_marker not in text:
        raise ValueError("RESULTS.md is missing the agent evaluation markers")
    lines = [
        "## M6 authored scenario harness (fixture backend)",
        "",
        "These values are from 40 authored context fixtures and a deterministic backend,",
        "not from a learned LLM or a live fault experiment. Root-cause IDs are absent in",
        "the fixture backend, so the accuracy numbers are a harness baseline only.",
        "The fixture backend has no model token count. Semantic citation precision,",
        "model cost, and a full hosted-model comparison remain unmeasured.",
        "",
        "| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | "
        "Citation ID validity | Median summary time |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("single_shot_rag", "Single-shot RAG fixture"),
                       ("agent", "Five-node agent fixture")):
        row = report[key]
        lines.append(
            f"| {label} | {row['root_cause_accuracy_at_1']:.3f} | "
            f"{row['root_cause_accuracy_at_3']:.3f} | "
            f"{row['runbook_recall_at_3']:.3f} | "
            f"{row['false_alarm_abstention']:.3f} | "
            f"{row['citation_id_validity_not_precision']:.3f} | "
            f"{row['median_time_to_first_summary_seconds']:.4f} s |"
        )
    replacement = start_marker + "\n" + "\n".join(lines) + "\n" + end_marker
    prefix, tail = text.split(start_marker, 1)
    _, suffix = tail.split(end_marker, 1)
    destination.write_text(prefix + replacement + suffix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("fixture", "ollama", "gemini"), default="fixture")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids",
                        help="Run only this exact fixture ID; may be repeated")
    parser.add_argument("--capture-outputs", action="store_true",
                        help="Include per-scenario claims and fixture context in the report")
    parser.add_argument("--update-results", action="store_true")
    parser.add_argument("--export-citations", type=Path,
                        help="Write unlabeled claim/source pairs for human semantic review")
    parser.add_argument("--report-path", type=Path,
                        help="Write JSON report; full runs have a default path")
    options = parser.parse_args()
    if (options.scenario_ids or options.capture_outputs) and options.report_path is None:
        parser.error("--scenario-id and --capture-outputs require --report-path")
    report = run(options.backend, options.limit,
                 collect_citations=bool(options.export_citations),
                 scenario_ids=options.scenario_ids,
                 capture_outputs=options.capture_outputs)
    if options.export_citations:
        write_review_queue(options.export_citations, report["citation_review_items"])
    if (options.limit is None and options.scenario_ids is None) or options.report_path is not None:
        output = options.report_path or ROOT / "eval" / f"agent_results_{options.backend}.json"
        saved = {k: v for k, v in report.items() if k != "citation_review_items"}
        output.write_text(json.dumps(saved, indent=2) + "\n")
    if options.update_results:
        if options.backend != "fixture" or options.limit is not None or options.scenario_ids:
            raise ValueError("Only the full 40-fixture run updates RESULTS.md")
        update_results(report)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("rows", "outputs", "citation_review_items")}, indent=2))
