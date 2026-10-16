"""Evaluate reproducible retrieval rankings against 40 fixed human-authored queries."""

from __future__ import annotations

import json
from pathlib import Path

from opspilot.retrieval import search
from opspilot.storage import make_engine, session_factory

ROOT = Path(__file__).resolve().parents[1]


def evaluate(mode: str, cases: list[dict]) -> dict:
    engine = make_engine()
    hits = 0
    reciprocal_ranks = []
    with session_factory(engine)() as session:
        for case in cases:
            results = search(session, case["query"], mode=mode, limit=5)
            rank = next(
                (i for i, row in enumerate(results, 1) if row["source_id"] in case["relevant"]),
                None,
            )
            hits += rank is not None
            reciprocal_ranks.append(1 / rank if rank is not None else 0)
    return {
        "queries": len(cases),
        "recall_at_5": hits / len(cases),
        "mrr_at_5": sum(reciprocal_ranks) / len(cases),
    }


if __name__ == "__main__":
    cases = json.loads((ROOT / "eval" / "retrieval_queries.json").read_text())
    report = {
        mode: evaluate(mode, cases) for mode in ("bm25", "vector", "hybrid", "rerank", "lexical")
    }
    destination = ROOT / "eval" / "retrieval_results.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
