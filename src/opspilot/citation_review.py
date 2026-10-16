"""Prepare claim/source pairs for human semantic-citation review."""

from __future__ import annotations

import json
from pathlib import Path


def source_evidence(source_id: str, alert: dict, context: dict) -> dict | None:
    """Return the exact supplied evidence object behind a cited source ID."""
    fingerprint = alert.get("fingerprint") or alert.get("labels", {}).get("alertname", "unknown")
    alert_id = f"alert:{fingerprint}"
    if source_id == alert_id:
        return alert
    if source_id == "policy:read-only":
        return {"policy": "read-only investigation; changes require approval"}
    for result in context.values():
        if not isinstance(result, dict):
            continue
        if result.get("source_id") == source_id:
            return result
        for key in ("results", "deploys", "jobs"):
            for item in result.get(key, []):
                if not isinstance(item, dict):
                    continue
                identifier = item.get("source_id") or item.get("number")
                if str(identifier) == source_id:
                    return item
                if key == "jobs" and item.get("id") is not None:
                    if f"backup:{item['id']}" == source_id:
                        return item
    return None


def review_items(scenario_id: str, path: str, alert: dict, output: dict) -> list[dict]:
    """Emit one unlabeled review item per claim/citation pair, including the cited evidence."""
    items = []
    for kind in ("hypotheses", "recommendations"):
        for claim_index, claim in enumerate(output.get(kind, [])):
            for source_id in claim.get("source_ids", []):
                items.append({
                    "scenario_id": scenario_id,
                    "path": path,
                    "claim_kind": kind,
                    "claim_index": claim_index,
                    "claim_text": claim.get("text", ""),
                    "source_id": source_id,
                    "source_evidence": source_evidence(
                        source_id, alert, output.get("context", {})
                    ),
                    "supports_claim": None,
                    "reviewer_note": "",
                })
    return items


def write_review_queue(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items))


def summarize_review(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    labeled = [row for row in rows if isinstance(row.get("supports_claim"), bool)]
    return {
        "pairs": len(rows),
        "labeled_pairs": len(labeled),
        "semantic_citation_precision": (
            sum(row["supports_claim"] for row in labeled) / len(labeled) if labeled else None
        ),
    }
