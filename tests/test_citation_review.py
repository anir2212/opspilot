"""Blinded citation-review harness: selection, packet, sealed key, and refusal rules.

All labels here are synthetic test inputs that exercise the arithmetic; none is a
human judgment about the model.
"""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from opspilot.agent import OllamaBackend
from opspilot.citation_review import (
    REDACTED,
    LabelFileError,
    build_packet,
    cohen_kappa,
    eligible_pairs,
    review_items,
    score_review,
    select_pairs,
    wilson_interval,
    write_review_queue,
)

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = {"backend": "ollama", "model": "test-model:1b", "model_digest": "abc123"}


def synthetic_rows(count: int = 40) -> list[dict]:
    rows = []
    for index in range(count):
        scenario = f"payment_errors_case{index}"
        alert = {
            "fingerprint": f"fixture-{scenario}",
            "labels": {"alertname": "ServiceDegraded", "service": "payments"},
        }
        output = {
            "hypotheses": [{"text": f"Claim {index} about {scenario}",
                            "source_ids": [f"alert:fixture-{scenario}"]}],
            "recommendations": [{"text": f"Follow runbook step {index}",
                                 "source_ids": ["RB-01"]}],
            "context": {"runbooks": {"source_id": "fixture:runbooks", "results": [
                {"source_id": "RB-01", "body": f"Runbook body {index}"}
            ]}},
        }
        path = "agent" if index % 2 else "single_shot_rag"
        rows.extend(review_items(scenario, path, alert, output, PROVENANCE))
    return rows


@pytest.fixture
def packet(tmp_path):
    export = tmp_path / "export.jsonl"
    write_review_queue(export, synthetic_rows())
    selection = build_packet(export, tmp_path / "review", n=20, seed=7)
    return tmp_path / "review", selection


def write_labels(path: Path, key_path: Path, labels: dict[str, str]) -> Path:
    key = json.loads(key_path.read_text())["items"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["item_id", "label", "note", "claim_text"])
        writer.writeheader()
        for item_id, label in labels.items():
            writer.writerow({"item_id": item_id, "label": label, "note": "",
                             "claim_text": key[item_id]["claim_text_shown"]})
    return path


def key_ids(review_dir: Path) -> list[str]:
    return sorted(json.loads((review_dir / "sealed" / "key.json").read_text())["items"])


def test_review_items_record_provenance_origin_and_resolution():
    alert = {"fingerprint": "f1", "labels": {}}
    output = {
        "hypotheses": [
            {"text": "Model claim", "source_ids": ["alert:f1", "made-up"]},
            {"text": "Hypothesis unavailable", "source_ids": ["alert:f1"], "fallback": True},
        ],
        "context": {},
    }
    items = review_items("s1", "agent", alert, output, PROVENANCE)
    assert [item["source_resolved"] for item in items] == [True, False, True]
    assert [item["claim_origin"] for item in items] == [
        "backend", "backend", "validator_fallback"
    ]
    assert all(item["model_digest"] == "abc123" for item in items)
    assert all(item["supports_claim"] is None for item in items)


def test_eligible_pairs_counts_every_exclusion():
    rows = synthetic_rows(4)
    duplicate = dict(rows[0])
    fallback = dict(rows[0], claim_text="other", claim_origin="validator_fallback")
    unresolved = dict(rows[0], claim_text="other2", source_resolved=False,
                      source_evidence=None)
    fixture = dict(rows[0], claim_text="other3", backend="fixture")
    pool, counts = eligible_pairs(rows + [duplicate, fallback, unresolved, fixture])
    assert len(pool) == 8
    assert counts == {
        "exported": 12, "eligible": 8, "excluded_duplicate_pair": 1,
        "excluded_validator_fallback": 1, "excluded_unresolved_source": 1,
        "excluded_other_backend": 1,
    }


def test_selection_is_seeded_and_independent_of_export_order():
    pool, _ = eligible_pairs(synthetic_rows())
    first = select_pairs(pool, 20, seed=3)
    assert select_pairs(list(reversed(pool)), 20, seed=3) == first
    assert select_pairs(pool, 20, seed=4) != first
    with pytest.raises(ValueError):
        select_pairs(pool, len(pool) + 1, seed=3)
    with pytest.raises(ValueError):
        select_pairs(pool, 0, seed=3)


def test_packet_is_blinded_and_key_is_sealed_separately(packet):
    review_dir, selection = packet
    assert selection["n"] == 20 and selection["human_labels"] == 0
    assert selection["model_digests"] == ["abc123"]
    shown = "".join(
        path.read_text() for path in (review_dir / "packet").iterdir()
    )
    for hidden in ("payment_errors_case", "fixture-payment", "test-model", "abc123",
                   "single_shot_rag", "source_resolved", "scenario_id", "\"path\""):
        assert hidden not in shown
    assert REDACTED in shown
    rows = list(csv.DictReader((review_dir / "packet" / "labels_A.csv").open()))
    assert [row["item_id"] for row in rows] == key_ids(review_dir)
    assert all(row["label"] == "" for row in rows)
    assert (review_dir / "packet" / "labels_B.csv").read_text() == (
        review_dir / "packet" / "labels_A.csv"
    ).read_text()
    key = json.loads((review_dir / "sealed" / "key.json").read_text())
    item = next(iter(key["items"].values()))
    assert item["scenario_id"].startswith("payment_errors_case")
    assert "supports_claim" not in item


def test_packet_build_is_reproducible_and_enforces_minimum(tmp_path):
    export = tmp_path / "export.jsonl"
    write_review_queue(export, synthetic_rows())
    build_packet(export, tmp_path / "a", n=20, seed=11)
    build_packet(export, tmp_path / "b", n=20, seed=11)
    for name in ("packet/labels_A.csv", "packet/packet.txt", "sealed/key.json"):
        assert (tmp_path / "a" / name).read_text() == (tmp_path / "b" / name).read_text()
    with pytest.raises(ValueError, match="at least 20"):
        build_packet(export, tmp_path / "c", n=19, seed=11)
    with pytest.raises(ValueError, match="Only"):
        build_packet(export, tmp_path / "d", n=20, seed=11, backend="fixture")


def test_single_labeller_precision_excludes_ambiguous(packet, tmp_path):
    review_dir, _ = packet
    ids = key_ids(review_dir)
    labels = {item: "supports_claim" for item in ids[:12]}
    labels.update({item: "unsupported" for item in ids[12:16]})
    labels.update({item: "ambiguous" for item in ids[16:]})
    report = score_review(review_dir / "sealed" / "key.json",
                          [write_labels(tmp_path / "labels_A.csv",
                                        review_dir / "sealed" / "key.json", labels)])
    result = report["labellers"]["labels_A"]
    assert result["precision"] == 0.75
    assert result["precision_denominator"] == 16
    assert result["ambiguous"] == 4 and result["unsupported"] == 4
    assert result["precision_wilson95"] == wilson_interval(12, 16)
    assert sum(path["labelled_items"] for path in result["by_path"].values()) == 20
    assert "agreement" not in report


def test_two_labellers_report_agreement_kappa_and_consensus(packet, tmp_path):
    review_dir, _ = packet
    key_path = review_dir / "sealed" / "key.json"
    ids = key_ids(review_dir)
    first = {item: "supports_claim" if index < 10 else "unsupported"
             for index, item in enumerate(ids)}
    second = dict(first)
    for item in ids[:2]:
        second[item] = "unsupported"
    second[ids[19]] = "ambiguous"
    report = score_review(key_path, [
        write_labels(tmp_path / "labels_A.csv", key_path, first),
        write_labels(tmp_path / "labels_B.csv", key_path, second),
    ])
    agreement = report["agreement"]
    assert agreement["agreements"] == 17 and agreement["disagreements"] == 3
    assert agreement["disagreement_item_ids"] == sorted([ids[0], ids[1], ids[19]])
    assert agreement["cohen_kappa"] == cohen_kappa(
        [first[item] for item in ids], [second[item] for item in ids]
    )
    assert report["consensus"]["supports_claim"] == 8
    assert report["consensus"]["unsupported"] == 9
    assert report["consensus"]["precision"] == round(8 / 17, 4)


def test_scorer_never_reads_citation_validity(packet, tmp_path):
    review_dir, _ = packet
    key_path = review_dir / "sealed" / "key.json"
    labels = {item: "unsupported" for item in key_ids(review_dir)}
    label_path = write_labels(tmp_path / "labels_A.csv", key_path, labels)
    before = score_review(key_path, [label_path])
    key = json.loads(key_path.read_text())
    for row in key["items"].values():
        row["source_resolved"] = True
        row["source_id"] = "RB-01"
        row["supports_claim"] = True
    key_path.write_text(json.dumps(key))
    assert score_review(key_path, [label_path]) == before
    assert before["labellers"]["labels_A"]["precision"] == 0.0


@pytest.mark.parametrize("mutation, message", [
    ("drop", "missing item_ids"),
    ("unknown", "unknown item_id"),
    ("duplicate", "duplicate item_id"),
    ("blank", "invalid label ''"),
    ("invalid", "invalid label 'yes'"),
    ("altered", "was altered"),
])
def test_scorer_refuses_inconsistent_label_files(packet, tmp_path, mutation, message):
    review_dir, _ = packet
    key_path = review_dir / "sealed" / "key.json"
    path = write_labels(tmp_path / "labels_A.csv", key_path,
                        {item: "supports_claim" for item in key_ids(review_dir)})
    rows = list(csv.DictReader(path.open()))
    if mutation == "drop":
        rows = rows[1:]
    elif mutation == "unknown":
        rows.append(dict(rows[0], item_id="CR-notreal"))
    elif mutation == "duplicate":
        rows.append(dict(rows[0]))
    elif mutation == "blank":
        rows[0]["label"] = ""
    elif mutation == "invalid":
        rows[0]["label"] = "yes"
    else:
        rows[0]["claim_text"] = "edited"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(LabelFileError, match=message):
        score_review(key_path, [path])


def test_scorer_rejects_missing_columns_and_same_file_twice(packet, tmp_path):
    review_dir, _ = packet
    key_path = review_dir / "sealed" / "key.json"
    bad = tmp_path / "labels_bad.csv"
    bad.write_text("item_id,verdict\n")
    with pytest.raises(LabelFileError, match="missing columns"):
        score_review(key_path, [bad])
    good = write_labels(tmp_path / "labels_A.csv", key_path,
                        {item: "ambiguous" for item in key_ids(review_dir)})
    with pytest.raises(ValueError, match="distinct"):
        score_review(key_path, [good, good])
    with pytest.raises(ValueError, match="one or two"):
        score_review(key_path, [])
    report = score_review(key_path, [good])
    assert report["labellers"]["labels_A"]["precision"] is None
    assert report["labellers"]["labels_A"]["precision_wilson95"] is None


def test_kappa_and_wilson_reference_values():
    assert cohen_kappa(["supports_claim", "unsupported"] * 5,
                       ["supports_claim", "unsupported"] * 5) == 1.0
    assert cohen_kappa(["ambiguous"] * 3, ["ambiguous"] * 3) is None
    # 2x2 example: observed 0.7, expected 0.5 -> kappa 0.4.
    first = ["supports_claim"] * 5 + ["unsupported"] * 5
    second = ["supports_claim"] * 4 + ["unsupported"] + ["supports_claim"] * 2 + [
        "unsupported"] * 3
    assert cohen_kappa(first, second) == 0.4
    assert wilson_interval(8, 10) == [0.4902, 0.9433]
    assert wilson_interval(0, 0) is None


def test_cli_scores_or_refuses(packet, tmp_path):
    review_dir, _ = packet
    key_path = review_dir / "sealed" / "key.json"
    labels = write_labels(tmp_path / "labels_A.csv", key_path,
                          {item: "supports_claim" for item in key_ids(review_dir)})
    command = [sys.executable, "eval/citation_packet.py", "score", "--key", str(key_path),
               "--labels", str(labels), "--out", str(tmp_path / "scores.json")]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["labellers"]["labels_A"]["precision"] == 1.0
    assert (tmp_path / "scores.json").is_file()
    labels.write_text(labels.read_text().replace("supports_claim", "maybe", 1))
    refused = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert refused.returncode == 2 and "Refusing to score" in refused.stderr


def test_ollama_digest_lookup_and_missing_model_blocks_export(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [
            {"name": "qwen2.5:0.5b", "model": "qwen2.5:0.5b", "digest": "d1"}
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("OPSPILOT_OLLAMA_MODEL", "qwen2.5:0.5b")
    assert OllamaBackend(client).model_digest() == "d1"
    monkeypatch.setenv("OPSPILOT_OLLAMA_MODEL", "absent:7b")
    assert OllamaBackend(client).model_digest() is None

    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
    assert spec and spec.loader
    run_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_eval)
    monkeypatch.setattr(run_eval, "OllamaBackend", lambda: OllamaBackend(client))
    with pytest.raises(RuntimeError, match="not available locally"):
        run_eval.run("ollama", limit=1, collect_citations=True)
