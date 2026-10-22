"""Build a blinded citation-review packet, or score completed human labels.

Examples:
  uv run python eval/citation_packet.py build \
      --export eval/citation_review/export_ollama.jsonl --out-dir eval/citation_review
  uv run python eval/citation_packet.py score --key eval/citation_review/sealed/key.json \
      --labels eval/citation_review/packet/labels_A.csv eval/citation_review/packet/labels_B.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from opspilot.citation_review import LabelFileError, build_packet, score_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Select pairs and write packet + sealed key")
    build.add_argument("--export", type=Path, required=True)
    build.add_argument("--out-dir", type=Path, required=True)
    build.add_argument("--n", type=int, default=30)
    build.add_argument("--seed", type=int, default=20261020)
    build.add_argument("--backend", default="ollama")
    build.add_argument("--labellers", nargs="+", default=["A", "B"])
    score = commands.add_parser("score", help="Score one or two completed labeller files")
    score.add_argument("--key", type=Path, required=True)
    score.add_argument("--labels", type=Path, nargs="+", required=True)
    score.add_argument("--out", type=Path, help="Optional JSON destination")
    options = parser.parse_args(argv)
    if options.command == "build":
        result = build_packet(options.export, options.out_dir, options.n, options.seed,
                              options.backend, tuple(options.labellers))
    else:
        try:
            result = score_review(options.key, options.labels)
        except LabelFileError as error:
            print(f"Refusing to score: {error}", file=sys.stderr)
            return 2
        if options.out:
            options.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
