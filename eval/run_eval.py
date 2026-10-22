"""Evaluate 40 fixture scenarios; a real model run is opt-in and separately labeled."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from statistics import median

import yaml
from pydantic import BaseModel, Field

from opspilot.agent import (
    DeterministicBackend,
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


def load_scenarios(limit: int | None = None) -> list[Scenario]:
    paths = sorted((ROOT / "eval" / "scenarios").glob("*.yaml"))
    scenarios = [Scenario.model_validate(yaml.safe_load(path.read_text())) for path in paths]
    if len(scenarios) != 40:
        raise ValueError(f"Expected 40 committed fixtures, found {len(scenarios)}")
    if len({scenario.id for scenario in scenarios}) != 40:
        raise ValueError("Scenario IDs must be unique")
    return scenarios[:limit] if limit is not None else scenarios


def score(scenario: Scenario, output: dict) -> dict:
    hypotheses = output.get("hypotheses", [])
    recommendations = output.get("recommendations", [])
    causes = [claim.get("cause_id") for claim in hypotheses if claim.get("cause_id")]
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
            not causes and not cited_runbooks
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
    }


def summarize(rows: list[dict]) -> dict:
    def average(key):
        values = [row[key] for row in rows if row[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "scenario_count": len(rows),
        "root_cause_accuracy_at_1": average("root_at_1"),
        "root_cause_accuracy_at_3": average("root_at_3"),
        "false_alarm_abstention": average("false_alarm_abstained"),
        "runbook_recall_at_3": average("runbook_recall_at_3"),
        "citation_id_validity_not_precision": average("citation_id_validity"),
        "citation_semantic_precision": None,
        "median_time_to_first_summary_seconds": round(
            median(row["time_to_first_summary_seconds"] for row in rows), 4
        ),
        "mean_tool_calls_per_scenario": average("tool_calls"),
        "mean_input_tokens_per_scenario": average("input_tokens"),
        "mean_output_tokens_per_scenario": average("output_tokens"),
        "failed_scenarios": sum(row["failed"] for row in rows),
        "cost_usd_per_incident": None,
    }


def run(backend_name: str = "fixture", limit: int | None = None,
        collect_citations: bool = False) -> dict:
    scenarios = load_scenarios(limit)
    backend = OllamaBackend() if backend_name == "ollama" else DeterministicBackend()
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
                     "error": type(exc).__name__}
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
            error = type(exc).__name__
        rag = {
            "hypotheses": claims, "recommendations": claims,
            "context": context, "duration_seconds": time.perf_counter() - started,
            "error": error,
        }
        if before_in is not None:
            rag["input_tokens"] = backend.input_tokens - before_in
            rag["output_tokens"] = backend.output_tokens - before_out
        rag_rows.append(score(scenario, rag))
        if collect_citations:
            citation_review_items.extend(
                review_items(scenario.id, "single_shot_rag", scenario.alert, rag, provenance)
            )
    report = {
        "backend": backend_name,
        "model": getattr(backend, "model", None),
        "model_digest": model_digest,
        "scope": "authored context fixtures; no live fault injection in this harness run",
        "single_shot_rag": summarize(rag_rows),
        "agent": summarize(agent_rows),
        "rows": {"single_shot_rag": rag_rows, "agent": agent_rows},
    }
    if collect_citations:
        report["citation_review_items"] = citation_review_items
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
        "Semantic citation precision, token counts, model cost, and a hosted-model",
        "comparison remain `TBD` until independently measured.",
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
    local_path = ROOT / "eval" / "agent_results_ollama.json"
    if local_path.is_file():
        local = json.loads(local_path.read_text())
        lines.extend([
            "",
            f"### Local LLM: {local['model']}",
            "",
            "The same authored fixtures were evaluated with an actual local model. "
            "This is a development benchmark, not a production incident estimate. "
            "No hosted backend was run or charged.",
            "",
            "| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | "
            "Citation ID validity | Median output time | Mean input/output tokens |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for key, label in (("single_shot_rag", "Single-shot RAG"),
                           ("agent", "Five-node agent")):
            row = local[key]
            lines.append(
                f"| {label} | {row['root_cause_accuracy_at_1']:.3f} | "
                f"{row['root_cause_accuracy_at_3']:.3f} | "
                f"{row['runbook_recall_at_3']:.3f} | "
                f"{row['false_alarm_abstention']:.3f} | "
                f"{row['citation_id_validity_not_precision']:.3f} | "
                f"{row['median_time_to_first_summary_seconds']:.3f} s | "
                f"{row['mean_input_tokens_per_scenario']:.1f} / "
                f"{row['mean_output_tokens_per_scenario']:.1f} |"
            )
        lines.extend([
            "",
            "`citation_id_validity` only checks whether an ID occurs in supplied context. "
            "It is **not** semantic citation precision. The agent abstained from "
            "diagnosing 4/8 false alarms; the single-shot path abstained from 1/8. "
            "Median time is local first output, not alert-to-chat latency. "
            "Dollar cost remains `TBD` because hardware and electricity cost were not measured.",
        ])
    replacement = start_marker + "\n" + "\n".join(lines) + "\n" + end_marker
    prefix, tail = text.split(start_marker, 1)
    _, suffix = tail.split(end_marker, 1)
    destination.write_text(prefix + replacement + suffix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("fixture", "ollama"), default="fixture")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--update-results", action="store_true")
    parser.add_argument("--export-citations", type=Path,
                        help="Write unlabeled claim/source pairs for human semantic review")
    parser.add_argument("--report-path", type=Path,
                        help="Full-run JSON path (default eval/agent_results_<backend>.json)")
    options = parser.parse_args()
    report = run(options.backend, options.limit, collect_citations=bool(options.export_citations))
    if options.export_citations:
        write_review_queue(options.export_citations, report["citation_review_items"])
    if options.limit is None:
        output = options.report_path or ROOT / "eval" / f"agent_results_{options.backend}.json"
        saved = {k: v for k, v in report.items() if k != "citation_review_items"}
        output.write_text(json.dumps(saved, indent=2) + "\n")
    if options.update_results:
        if options.backend != "fixture" or options.limit is not None:
            raise ValueError("Only the full 40-fixture run updates RESULTS.md")
        update_results(report)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("rows", "citation_review_items")}, indent=2))
