"""End-to-end dry-run pipeline test (§5.5).

``run_diagnosis.py --dry-run`` must run the *real* middleware stack over a
scripted model + fake session (no device, no network) and emit a complete set of
artifacts: ``evidence.jsonl`` + ``summary.json`` + ``report.html`` +
``status.json``. This asserts the offline pipeline is intact end to end and the
report is non-empty + base64-free.
"""

from __future__ import annotations

import json
from pathlib import Path

import run_diagnosis


def test_dry_run_pipeline(tmp_path, capsys):
    rc = run_diagnosis.main(
        ["run", "--dry-run", "--output-dir", str(tmp_path), "--quiet", "打开设置冒烟"]
    )
    assert rc == 0, "dry-run pipeline must exit 0 when the pipeline is intact"

    run_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected exactly one run dir, got {run_dirs}"
    run_dir = run_dirs[0]

    evidence = run_dir / "evidence.jsonl"
    summary_path = run_dir / "summary.json"
    report_path = run_dir / "report.html"
    status_path = run_dir / "status.json"
    for artifact in (evidence, summary_path, report_path, status_path):
        assert artifact.exists(), f"missing artifact: {artifact.name}"

    # evidence: valid JSONL with the terminal event.
    events = [json.loads(l) for l in evidence.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert events
    kinds = {e["event"] for e in events}
    assert {"run_start", "model_request", "tool_invoke", "tool_observation", "run_end"} <= kinds

    # summary: the scripted script (read_screen -> update_task_doc -> tap ->
    # finish) declares completion, so the verdict is success.
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "success"
    assert summary["taskdoc_final"]["terminal_state"] == "all_completed"
    # dry-run disclaimer recorded (does not validate real grounding/finish).
    assert any("dry-run" in note for note in summary.get("notes", []))

    # report: non-empty, base64-free, has the overview blocks.
    html = report_path.read_text(encoding="utf-8")
    assert len(html) > 5000
    assert "data:image" not in html
    assert "终局裁定" in html and "任务板终态" in html and "80/20 三件事" in html
    # the step-by-step replay is embedded (replay list + renderer).
    assert "逐步回放" in html and "renderReplay" in html
    assert summary.get("replay"), "summary must carry a per-step replay list"

    # status: completed.
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["dry_run"] is True

    # stdout is suppressed by --quiet.
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_analyze_and_report_subcommands_from_dry_run(tmp_path):
    # First produce a run, then re-derive + re-render from artifacts (no re-run).
    rc = run_diagnosis.main(
        ["run", "--dry-run", "--output-dir", str(tmp_path), "--quiet", "再渲染冒烟"]
    )
    assert rc == 0
    run_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    # analyze accepts a run DIR (resolves evidence.jsonl within it).
    out_summary = tmp_path / "re-summary.json"
    rc = run_diagnosis.main(["analyze", str(run_dir), "--output", str(out_summary)])
    assert rc == 0
    assert out_summary.exists()
    resummary = json.loads(out_summary.read_text(encoding="utf-8"))
    assert resummary["verdict"] == "success"

    # report accepts a summary.json and re-renders the HTML.
    out_report = tmp_path / "re-report.html"
    rc = run_diagnosis.main(
        ["report", str(run_dir / "summary.json"), "--output", str(out_report)]
    )
    assert rc == 0
    assert out_report.exists()
    assert "data:image" not in out_report.read_text(encoding="utf-8")
