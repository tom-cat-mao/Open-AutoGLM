"""Score grounding predictions against a benchmark manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.grounding.datasets import read_manifest
from bench.grounding.reporting import build_summary, enrich_prediction, read_jsonl, write_jsonl


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output", required=True, help="Scored prediction JSONL path")
    parser.add_argument("--summary-output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cases = {str(case.get("id")): case for case in read_manifest(Path(args.manifest))}
    predictions = read_jsonl(Path(args.predictions))
    scored = []
    missing_cases = []
    for prediction in predictions:
        case_id = str(prediction.get("case_id"))
        case = cases.get(case_id)
        if case is None:
            missing_cases.append(case_id)
            continue
        scored.append(enrich_prediction(prediction, case))
    if missing_cases:
        raise SystemExit(f"{len(missing_cases)} predictions did not match manifest cases")

    output_path = Path(args.output)
    write_jsonl(output_path, scored)
    summary = build_summary(scored, metadata={"manifest": args.manifest, "predictions": args.predictions})
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
