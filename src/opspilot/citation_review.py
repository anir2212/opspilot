"""Prepare, blind, and score human semantic-citation review of claim/source pairs.

Support labels come only from human labeller files. Nothing here infers support from
whether a cited ID exists in the agent's context.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path

LABELS = ("supports_claim", "ambiguous", "unsupported")
REDACTED = "[scenario-id-redacted]"
LABEL_COLUMNS = ("item_id", "label", "note", "claim_text", "source_text")
PRECISION_DEFINITION = (
    "precision = supports_claim / (supports_claim + unsupported); ambiguous labels are "
    "excluded from the denominator and reported separately"
)
CONSENSUS_DEFINITION = (
    "consensus precision uses only items where both labellers chose the same "
    "non-ambiguous label; disagreements need adjudication and are excluded"
)


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


def review_items(
    scenario_id: str, path: str, alert: dict, output: dict, provenance: dict | None = None
) -> list[dict]:
    """Emit one unlabeled review item per claim/citation pair, including the cited evidence."""
    items = []
    for kind in ("hypotheses", "recommendations"):
        for claim_index, claim in enumerate(output.get(kind, [])):
            for source_id in claim.get("source_ids", []):
                evidence = source_evidence(source_id, alert, output.get("context", {}))
                items.append({
                    **(provenance or {}),
                    "scenario_id": scenario_id,
                    "alert_fingerprint": alert.get("fingerprint"),
                    "path": path,
                    "claim_kind": kind,
                    "claim_index": claim_index,
                    "claim_origin": "validator_fallback" if claim.get("fallback") else "backend",
                    "claim_text": claim.get("text", ""),
                    "source_id": source_id,
                    "source_resolved": evidence is not None,
                    "source_evidence": evidence,
                    "supports_claim": None,
                    "reviewer_note": "",
                })
    return items


def write_review_queue(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items))


def read_review_queue(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize_review(path: Path) -> dict:
    rows = read_review_queue(path)
    labeled = [row for row in rows if isinstance(row.get("supports_claim"), bool)]
    return {
        "pairs": len(rows),
        "labeled_pairs": len(labeled),
        "semantic_citation_precision": (
            sum(row["supports_claim"] for row in labeled) / len(labeled) if labeled else None
        ),
    }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def eligible_pairs(items: list[dict], backend: str = "ollama") -> tuple[list[dict], dict]:
    """Keep resolvable, backend-written, de-duplicated pairs from one backend.

    Validator fallbacks are not model claims. Unresolved citations have no source text to
    judge, so they are counted separately instead of being labelled.
    """
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    pool = []
    for item in items:
        counts["exported"] += 1
        if item.get("backend") != backend:
            counts["excluded_other_backend"] += 1
        elif item.get("claim_origin") != "backend":
            counts["excluded_validator_fallback"] += 1
        elif not item.get("source_resolved") or item.get("source_evidence") is None:
            counts["excluded_unresolved_source"] += 1
        else:
            identity = (item["claim_text"], _canonical(item["source_evidence"]))
            if identity in seen:
                counts["excluded_duplicate_pair"] += 1
                continue
            seen.add(identity)
            pool.append(item)
    counts["eligible"] = len(pool)
    return pool, dict(counts)


def _stable_key(item: dict) -> str:
    return _sha256_text(_canonical({
        key: item.get(key)
        for key in ("scenario_id", "path", "claim_kind", "claim_index", "source_id")
    }))


def select_pairs(pool: list[dict], n: int, seed: int) -> list[dict]:
    """Seeded simple random sample, independent of export order."""
    if n < 1:
        raise ValueError("n must be positive")
    if len(pool) < n:
        raise ValueError(f"Only {len(pool)} eligible pairs; cannot select {n}")
    return random.Random(seed).sample(sorted(pool, key=_stable_key), n)


def _redact(text: str, tokens: list[str | None]) -> str:
    for token in sorted((token for token in tokens if token), key=len, reverse=True):
        text = text.replace(token, REDACTED)
    return text


def blinded_view(item: dict) -> tuple[str, str]:
    """Claim and source text with scenario-identifying IDs removed."""
    tokens = [item.get("scenario_id"), item.get("alert_fingerprint")]
    source_text = json.dumps(item["source_evidence"], indent=2, sort_keys=True,
                             ensure_ascii=False)
    return _redact(item["claim_text"], tokens), _redact(source_text, tokens)


def _write_label_template(path: Path, packet: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for row in packet:
            writer.writerow({"item_id": row["item_id"], "label": "", "note": "",
                             "claim_text": row["claim_text"],
                             "source_text": row["source_text"]})


def _write_readable(path: Path, packet: list[dict]) -> None:
    blocks = [
        "Blinded citation-review packet. Label each item in your own labels_<X>.csv using\n"
        f"exactly one of: {', '.join(LABELS)}. Follow docs/CITATION_RUBRIC.md.\n"
        "Do not open ../sealed/ or the export JSONL until both labellers have finished.\n"
    ]
    for row in packet:
        blocks.append(
            f"\n=== {row['item_id']} ===\nCLAIM:\n{row['claim_text']}\n\n"
            f"SOURCE:\n{row['source_text']}\n"
        )
    path.write_text("".join(blocks))


def build_packet(
    export_path: Path, out_dir: Path, n: int, seed: int, backend: str = "ollama",
    labellers: tuple[str, ...] = ("A", "B"), minimum: int = 20,
) -> dict:
    """Select pairs and write a blinded packet plus a separate sealed key."""
    if n < minimum:
        raise ValueError(f"Select at least {minimum} pairs")
    if len(set(labellers)) != len(labellers) or not labellers:
        raise ValueError("Labeller names must be distinct and non-empty")
    export_text = export_path.read_text()
    items = read_review_queue(export_path)
    pool, counts = eligible_pairs(items, backend)
    selected = select_pairs(pool, n, seed)
    rng = random.Random(f"item-ids:{seed}")
    item_ids: set[str] = set()
    while len(item_ids) < n:
        item_ids.add(f"CR-{rng.getrandbits(32):08x}")
    key_items = {}
    packet = []
    for item_id, item in zip(sorted(item_ids), selected, strict=True):
        claim_text, source_text = blinded_view(item)
        packet.append({"item_id": item_id, "claim_text": claim_text,
                       "source_text": source_text})
        hidden = {k: v for k, v in item.items() if k not in ("supports_claim", "reviewer_note")}
        key_items[item_id] = {**hidden, "claim_text_shown": claim_text,
                              "source_text_shown": source_text}
    packet_dir = out_dir / "packet"
    sealed_dir = out_dir / "sealed"
    packet_dir.mkdir(parents=True, exist_ok=True)
    sealed_dir.mkdir(parents=True, exist_ok=True)
    _write_readable(packet_dir / "packet.txt", packet)
    for name in labellers:
        _write_label_template(packet_dir / f"labels_{name}.csv", packet)
    selection = {
        "backend": backend,
        "models": sorted({str(item.get("model")) for item in selected}),
        "model_digests": sorted({str(item.get("model_digest")) for item in selected}),
        "export_file": export_path.name,
        "export_sha256": _sha256_text(export_text),
        "seed": seed,
        "n": n,
        "method": "seeded simple random sample of eligible pairs sorted by stable hash",
        "pool_counts": counts,
        "selected_by_path": dict(Counter(item["path"] for item in selected)),
        "redaction": f"scenario ID and alert fingerprint replaced with {REDACTED}",
        "labellers": list(labellers),
        "human_labels": 0,
    }
    key = {"selection": selection, "items": key_items}
    (sealed_dir / "key.json").write_text(json.dumps(key, indent=2, sort_keys=True) + "\n")
    return selection


class LabelFileError(ValueError):
    """A labeller file cannot be scored safely."""


def read_labels(path: Path, key_items: dict) -> dict[str, str]:
    """Validate one labeller CSV against the sealed key; refuse any inconsistency."""
    problems = []
    labels: dict[str, str] = {}
    seen: set[str] = set()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing_columns = {"item_id", "label"} - set(reader.fieldnames or [])
        if missing_columns:
            raise LabelFileError(f"{path.name}: missing columns {sorted(missing_columns)}")
        for line, row in enumerate(reader, start=2):
            item_id = (row.get("item_id") or "").strip()
            label = (row.get("label") or "").strip()
            if item_id not in key_items:
                problems.append(f"line {line}: unknown item_id {item_id!r}")
                continue
            if item_id in seen:
                problems.append(f"line {line}: duplicate item_id {item_id}")
                continue
            seen.add(item_id)
            if label not in LABELS:
                problems.append(f"line {line}: invalid label {label!r} for {item_id}")
                continue
            shown = row.get("claim_text")
            if shown is not None and shown != key_items[item_id]["claim_text_shown"]:
                problems.append(f"line {line}: claim_text for {item_id} was altered")
                continue
            labels[item_id] = label
    missing = sorted(set(key_items) - seen)
    if missing:
        problems.append(f"missing item_ids: {missing}")
    if problems:
        raise LabelFileError(f"{path.name}: " + "; ".join(problems))
    return labels


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    """Two-sided Wilson score interval for a binomial proportion."""
    if total == 0:
        return None
    phat = successes / total
    centre = phat + z * z / (2 * total)
    spread = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    scale = 1 + z * z / total
    return [round((centre - spread) / scale, 4), round((centre + spread) / scale, 4)]


def _counts(labels: dict[str, str]) -> dict:
    counts = Counter(labels.values())
    supports = counts["supports_claim"]
    unsupported = counts["unsupported"]
    denominator = supports + unsupported
    return {
        "labelled_items": len(labels),
        "supports_claim": supports,
        "unsupported": unsupported,
        "ambiguous": counts["ambiguous"],
        "precision_denominator": denominator,
        "precision": round(supports / denominator, 4) if denominator else None,
        "precision_wilson95": wilson_interval(supports, denominator),
    }


def cohen_kappa(first: list[str], second: list[str]) -> float | None:
    """Cohen's kappa over the three rubric labels; None when chance agreement is 1."""
    if len(first) != len(second) or not first:
        raise ValueError("Kappa needs two equal, non-empty label lists")
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / total
    a_counts, b_counts = Counter(first), Counter(second)
    expected = sum(a_counts[label] * b_counts[label] for label in LABELS) / total**2
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def score_review(key_path: Path, label_paths: list[Path]) -> dict:
    """Join one or two labeller files to the sealed key and compute review metrics.

    Only the human labels, item IDs, displayed claim text, and generation path are read
    from the key; citation-ID validity is never consulted.
    """
    if len(label_paths) not in (1, 2):
        raise ValueError("Provide one or two labeller files")
    if len({path.resolve() for path in label_paths}) != len(label_paths):
        raise ValueError("Labeller files must be distinct")
    key = json.loads(key_path.read_text())
    key_items = {
        item_id: {"claim_text_shown": row["claim_text_shown"], "path": row["path"]}
        for item_id, row in key["items"].items()
    }
    labellers = {path.stem: read_labels(path, key_items) for path in label_paths}
    if len(labellers) != len(label_paths):
        raise ValueError("Labeller file names must be distinct")
    report: dict = {
        "items_in_key": len(key_items),
        "backend": key["selection"]["backend"],
        "models": key["selection"]["models"],
        "model_digests": key["selection"]["model_digests"],
        "definition": PRECISION_DEFINITION,
        "labellers": {},
    }
    for name, labels in labellers.items():
        by_path = {
            path: _counts({k: v for k, v in labels.items() if key_items[k]["path"] == path})
            for path in sorted({row["path"] for row in key_items.values()})
        }
        report["labellers"][name] = {**_counts(labels), "by_path": by_path}
    if len(labellers) == 2:
        (name_a, labels_a), (name_b, labels_b) = labellers.items()
        ordered = sorted(key_items)
        first = [labels_a[item] for item in ordered]
        second = [labels_b[item] for item in ordered]
        agreed = {item: labels_a[item] for item in ordered if labels_a[item] == labels_b[item]}
        confusion = Counter(f"{a}|{b}" for a, b in zip(first, second, strict=True))
        consensus = _counts(agreed)
        report["agreement"] = {
            "labellers": [name_a, name_b],
            "agreements": len(agreed),
            "disagreements": len(ordered) - len(agreed),
            "percent_agreement": round(len(agreed) / len(ordered), 4),
            "cohen_kappa": cohen_kappa(first, second),
            "confusion": dict(sorted(confusion.items())),
            "disagreement_item_ids": [item for item in ordered if item not in agreed],
        }
        report["consensus"] = {**consensus, "definition": CONSENSUS_DEFINITION}
    return report
