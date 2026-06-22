#!/usr/bin/env python3
"""Render an interactive HTML report from an existing diagnosis summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_diagnosis import read_jsonl, render_html_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Render live diagnosis HTML report")
    parser.add_argument("summary", help="Path to summary.json")
    parser.add_argument("--trace", help="Optional trace JSONL")
    parser.add_argument("--output", help="Output HTML path")
    args = parser.parse_args()

    summary_path = Path(args.summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    trace_events = read_jsonl(Path(args.trace)) if args.trace else []
    html = render_html_report(summary, trace_events)
    output = Path(args.output) if args.output else summary_path.with_name("report.html")
    output.write_text(html, encoding="utf-8")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
