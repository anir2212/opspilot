"""Score a frozen independent runbook-query set without changing retrieval settings."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select

from opspilot.retrieval import search
from opspilot.storage import KnowledgeChunk, make_engine, session_factory

EXPECTED_SHA256 = "966df6939b701bb3789a446381c4cbf1edce1e92a132494963de0686ddadab45"
MODES = ("rerank", "bm25", "vector", "hybrid", "lexical", "agent_runbook_hybrid")


def evaluate(cases_path: Path) -> dict:
    raw = cases_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"Holdout hash mismatch: {digest}")
    cases = json.loads(raw)
    if len(cases) != 40 or any(set(row) != {"query", "relevant"} for row in cases):
        raise ValueError("Expected 40 query/relevant pairs")

    report = {
        "input_sha256": digest,
        "scope": (
            "independently authored runbook-query text; same synthetic corpus; "
            "no agent alert or LLM"
        ),
        "queries": len(cases),
        "candidate_chunks": 5,
        "modes": {},
    }
    with session_factory(make_engine())() as session:
        report["indexed_chunks"] = session.scalar(select(func.count()).select_from(KnowledgeChunk))
        if report["indexed_chunks"] != 160:
            raise ValueError(f"Expected 160 indexed chunks; found {report['indexed_chunks']}")
        for label in MODES:
            source_type = "runbook" if label == "agent_runbook_hybrid" else None
            mode = "hybrid" if source_type else label
            rows = []
            for index, case in enumerate(cases, 1):
                results = search(
                    session, case["query"], mode=mode, limit=5, source_type=source_type
                )
                retrieved = [item["source_id"] for item in results]
                rank = next(
                    (position for position, source in enumerate(retrieved, 1)
                     if source in case["relevant"]),
                    None,
                )
                rows.append({
                    "item": index,
                    "query": case["query"],
                    "relevant": case["relevant"],
                    "retrieved": retrieved,
                    "first_relevant_rank": rank,
                })
            report["modes"][label] = {
                "recall_at_5": sum(row["first_relevant_rank"] is not None for row in rows)
                / len(rows),
                "mrr_at_5": sum(
                    1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0
                    for row in rows
                ) / len(rows),
                "rows": rows,
            }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.cases)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "input_sha256": result["input_sha256"],
        "queries": result["queries"],
        "indexed_chunks": result["indexed_chunks"],
        "modes": {
            mode: {key: value for key, value in values.items() if key != "rows"}
            for mode, values in result["modes"].items()
        },
    }, indent=2))
