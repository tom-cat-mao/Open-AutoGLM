"""Regression tests for the live-diagnosis rules in run_diagnosis.py.

The 20260727 限速摩卡 live run exhausted max_steps in a decision-level loop
while the diagnosis report mis-attributed the failure to the reflection layer
with confidence="confirmed". These tests pin the fixed behavior:

* routine reflect_result failure_cause values stay out of the errors bucket;
* decision-loop signals produce a dedicated P1 decision finding ranked first;
* findings built only from weak verifier signals are never "confirmed".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents/skills/phone-agent-live-diagnosis/scripts/run_diagnosis.py"
)


def _load_run_diagnosis():
    spec = importlib.util.spec_from_file_location("run_diagnosis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_diagnosis", module)
    spec.loader.exec_module(module)
    return module


def _reflect_event(step: int, **payload) -> dict:
    return {
        "step_id": step,
        "node": "reflect",
        "event": "reflect_result",
        "timestamp": float(step),
        "payload": payload,
    }


def _record(**overrides) -> dict:
    record = {
        "success": False,
        "steps": 20,
        "max_steps": 20,
        "acceptance_round_count": 0,
        "finish_validation_status": None,
        "repeated_failure_count": 7,
        "error_layer": None,
        "error_code": None,
        "failure_cause": "unknown",
        "verifier_status": "success",
    }
    record.update(overrides)
    return record


def test_reflect_failure_cause_is_not_an_error() -> None:
    rd = _load_run_diagnosis()
    events = [
        _reflect_event(step, failure_cause="wrong_page", verifier_status="unknown")
        for step in range(1, 6)
    ]

    summary = rd.summarize_trace(events)

    assert summary["errors"] == []
    assert summary["step_count"] == 5


def test_real_error_events_still_land_in_errors_bucket() -> None:
    rd = _load_run_diagnosis()
    events = [
        _reflect_event(1, failure_cause="wrong_page"),
        {
            "step_id": 2,
            "node": "execute",
            "event": "execute_error",
            "timestamp": 2.0,
            "payload": {"error_code": "device_offline"},
        },
    ]

    summary = rd.summarize_trace(events)

    assert [item["event"] for item in summary["errors"]] == ["execute_error"]


def test_decision_loop_finding_outranks_weak_reflection_finding() -> None:
    rd = _load_run_diagnosis()
    events = [
        _reflect_event(
            step,
            failure_cause="unknown",
            verifier_result={"status": "unknown", "evidence": {"dynamic_change_only": True}},
        )
        for step in range(1, 21)
    ]
    summary = rd.summarize_trace(events)

    findings = rd.build_code_findings(_record(), summary)

    assert findings[0]["layer"] == "decision"
    assert findings[0]["severity"] == "P1"
    assert "budget_exhausted_no_finish" in findings[0]["matched_signals"]
    assert "repeated_failure_count" in findings[0]["matched_signals"]
    reflection = [f for f in findings if f["layer"] == "reflection"]
    assert reflection and reflection[0]["confidence"] != "confirmed"


def test_signal_steps_and_confidence_grading() -> None:
    rd = _load_run_diagnosis()
    events = [
        _reflect_event(step, repeated_action_detected=True, repeat_count=3)
        for step in range(1, 9)
    ]
    summary = rd.summarize_trace(events)

    signal_steps = rd.collect_signal_steps(summary)
    assert signal_steps["repeated_action_detected"] == [str(i) for i in range(1, 9)]
    assert signal_steps["avoid_repeating_ignored"] == [str(i) for i in range(1, 9)]

    assert rd.grade_confidence(["repeated_action_detected"], signal_steps, 8) == "confirmed"
    assert rd.grade_confidence(["repeated_action_detected"], signal_steps, 30) == "likely"
    assert rd.grade_confidence(["repeated_action_detected"], signal_steps, 100) == "needs-repro"
    # Record-level signals carry no per-step evidence; that is neutral.
    assert rd.grade_confidence(["budget_exhausted_no_finish"], {}, 20) == "likely"


def test_successful_run_keeps_info_finding() -> None:
    rd = _load_run_diagnosis()
    events = [_reflect_event(step, reflection_verdict="succeeded") for step in range(1, 4)]
    summary = rd.summarize_trace(events)

    findings = rd.build_code_findings(
        _record(
            success=True,
            steps=3,
            max_steps=20,
            repeated_failure_count=0,
            failure_cause=None,
        ),
        summary,
    )

    assert findings[0]["layer"] == "success"
    assert findings[0]["severity"] == "Info"
