#!/usr/bin/env python3
"""Analyze an existing PhoneAgent JSONL trace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_diagnosis import build_code_findings, build_recommendations, read_jsonl, summarize_trace, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PhoneAgent trace JSONL")
    parser.add_argument("trace", help="Path to trace JSONL")
    parser.add_argument("--result", help="Optional result.json")
    parser.add_argument("--output", help="Output JSON path")
    args = parser.parse_args()

    trace_events = read_jsonl(Path(args.trace))
    trace_summary = summarize_trace(trace_events)
    record = {}
    if args.result:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        rows = result.get("results") or []
        if rows:
            record = rows[0]
    findings = build_code_findings(record, trace_summary)
    payload = {
        "trace_summary": trace_summary,
        "code_findings": findings,
        "recommendations": build_recommendations(findings, record),
    }
    if args.output:
        write_json(Path(args.output), payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
