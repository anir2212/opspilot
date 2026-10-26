"""Compare the agent's old and current search queries on authored development fixtures."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from run_eval import load_scenarios
from sqlalchemy import func, select

from opspilot.agent import _evidence_query
from opspilot.retrieval import search
from opspilot.storage import KnowledgeChunk, make_engine, session_factory


def evaluate() -> dict:
    scenarios = [case for case in load_scenarios() if case.expected_runbooks]
    report: dict = {
        "scope": "authored development scenarios; runbook retrieval only, no LLM call",
        "candidate_chunks": 5,
        "search_mode": "hybrid",
        "source_type": "runbook",
        "scenarios": len(scenarios),
    }
    with session_factory(make_engine())() as session:
        report["indexed_chunks"] = session.scalar(select(func.count()).select_from(KnowledgeChunk))
        for label in ("legacy", "symptom_aware"):
            started = time.perf_counter()
            rows = []
            for case in scenarios:
                service = case.alert["labels"]["service"]
                query = (
                    f"{service} {case.alert['labels']['alertname']}"
                    if label == "legacy"
                    else _evidence_query(case.alert, service)
                )
                retrieved = search(session, query, mode="hybrid", limit=5, source_type="runbook")
                found = sorted(
                    {row["source_id"] for row in retrieved} & set(case.expected_runbooks)
                )
                rows.append({
                    "id": case.id,
                    "expected": case.expected_runbooks,
                    "retrieved": [row["source_id"] for row in retrieved],
                    "found": found,
                    "recall": len(found) / len(case.expected_runbooks),
                })
            report[label] = {
                "mean_expected_runbook_recall_at_5": sum(row["recall"] for row in rows) / len(rows),
                "full_credit_scenarios": sum(row["recall"] == 1 for row in rows),
                "zero_credit_scenarios": sum(row["recall"] == 0 for row in rows),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "rows": rows,
            }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = evaluate()
    payload = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.write_text(payload)
    print(json.dumps({
        "scenarios": result["scenarios"],
        "indexed_chunks": result["indexed_chunks"],
        "legacy": {key: value for key, value in result["legacy"].items() if key != "rows"},
        "symptom_aware": {
            key: value for key, value in result["symptom_aware"].items() if key != "rows"
        },
    }, indent=2))
