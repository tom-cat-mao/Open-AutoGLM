"""Report rendering tests (A5 rewrite).

Renders a summary fixture (with a per-step ``replay`` + evidence stream) through
:func:`render_html` and asserts the local-first full-fidelity report is complete
and safe against the guarantees that still hold:

* **base64-free** — the report never carries a screenshot payload (screenshots
  live on disk and are referenced by relative ``image.path``);
* **``<base target="_blank">``** — links open in a new tab (design carried over);
* the overview leads with the three blocks (终局裁定 / 任务板终态 / 80/20 三件事);
* the **step-by-step replay** renders a real screenshot ``<img>`` from
  ``image.path``, the model thinking, and the tool result;
* the JSON island is ``</script>``-escaped so a payload string containing
  ``</script>`` can never break out of the data island.
"""

from __future__ import annotations

from typing import Any

from report import _escape_report_data, render_html


def _summary_fixture() -> dict[str, Any]:
    return {
        "run_id": "t1",
        "created_at": "2026-01-01T00:00:00",
        "target": "打开设置",
        "verdict": "failed",
        "run_dir": "/tmp/run",
        "command": ["run_diagnosis.py", "run", "打开设置"],
        "duration_sec": 3.2,
        "steps": 4,
        "evidence_stream": "/tmp/run/evidence.jsonl",
        "trace": "/tmp/run/traces",
        "artifacts": {"summary": "/tmp/run/summary.json", "evidence": "/tmp/run/evidence.jsonl"},
        "terminal": {
            "finished": False,
            "finish_summary": None,
            "takeover_reason": None,
            "reason": "model_stopped",
            "returncode": 1,
        },
        "finish_gate": {
            "attempted": True,
            "accepted": False,
            "blocked_by_open_items": True,
            "open_items_at_finish": ["s2:付款"],
            "rejections": [{"step": 3, "class": "finish_blocked_open_items", "message": "路线仍有未完成项：s2:付款[pending]"}],
        },
        "taskdoc_final": {
            "goal_base": "买一杯咖啡",
            "amendments": ["选中杯"],
            "items": [
                {"id": "s1", "content": "选规格", "status": "completed", "reason": None, "evidence_note": "已选中杯"},
                {"id": "s2", "content": "付款", "status": "pending", "reason": None, "evidence_note": None},
            ],
            "facts": ["价格 ¥18"],
            "counts": {"total": 2, "completed": 1, "in_progress": 0, "pending": 1, "blocked": 0},
            "open_item_count": 1,
            "terminal_state": "has_open",
        },
        "stagnation": {"nudged": False, "nudge_step": None, "max_seen_states": 2, "stagnant_streak_peak": 0},
        "context": {"peak_message_count": 5, "peak_image_messages": 1, "pruned_screen_total": 2,
                    "taskdoc_pinned_every_step": True, "avg_context_chars": 480.0},
        "hitl": {"interrupts": 0, "decisions": [], "approvals": 0, "rejections": 0, "responds": 0,
                 "ask_user_count": 0, "take_over_count": 0},
        "tool_health": {"total_calls": 4, "total_errors": 1, "error_rate": 0.25,
                        "by_tool": {"finish": {"calls": 1, "ok": 0, "error": 1,
                                               "error_classes": {"finish_blocked_open_items": 1},
                                               "avg_latency_ms": 3.0, "p95_latency_ms": 3.0}}},
        "grounding": {"mark_addressing": {"by_mark_id": 2, "by_description": 1},
                      "resolve_failures": {"ambiguous": 0, "stale": 0, "no_match": 0},
                      "locate": {"calls": 0, "success": 0, "no_match": 0, "provider_error": 0},
                      "launch": {"resolved": 0, "denied": 0, "unknown": 0, "not_installed": 0, "ambiguous": 0}},
        "visual": {"tool_results_with_image": 3, "total_image_bytes": 12288, "first_image_step": 1, "last_image_step": 4},
        "model": {"calls": 4, "avg_latency_ms": None, "p95_latency_ms": None, "errors": 0,
                  "token_usage": {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540}},
        "replay": [
            {
                "step": 1,
                "thinking": "先看看当前屏幕再决定点哪里",
                "model_tool_calls": [{"name": "read_screen", "args": {}}],
                "usage": {"input_tokens": 300, "output_tokens": 80, "total_tokens": 380},
                "context": {"message_count": 2, "image_message_count": 1, "pruned_screen_count": 0,
                            "taskdoc_present": True, "context_chars": 200},
                "tool_calls": [
                    {"tool": "read_screen", "args": {}, "result_text": "[OBS] app=com.x screen#1\nmarks (3): a; b; c",
                     "result_truncated": False, "latency_ms": 5, "error": None, "class": "observation",
                     "obs": {"current_app": "com.x", "screen_seq": 1, "mark_count": 3},
                     "image": {"present": True, "screen_seq": 1, "bytes": 4096, "path": "screenshots/screen-1.png"}},
                ],
                "hitl": [],
            },
            {
                "step": 2,
                "thinking": "路线还有付款没做，先别 finish",
                "model_tool_calls": [{"name": "finish", "args": {"summary": "done"}}],
                "usage": None,
                "context": {"message_count": 4, "image_message_count": 1, "pruned_screen_count": 1,
                            "taskdoc_present": True, "context_chars": 420},
                "tool_calls": [
                    {"tool": "finish", "args": {"summary": "done", "evidence": ["x"]},
                     "result_text": "路线仍有未完成项：s2:付款[pending]", "result_truncated": False,
                     "latency_ms": 3, "error": None, "class": "finish_blocked_open_items",
                     "obs": None, "image": {"present": False, "screen_seq": None, "bytes": 0, "path": None}},
                ],
                "hitl": [],
            },
        ],
        "findings": [
            {"category": "finish_gate", "layer": "finish", "severity": "P0",
             "title": "完成门：路线未闭合", "count": 1, "examples": ["路线仍有未完成项：s2:付款[pending]"],
             "files": [{"path": "phone_agent/v2/tools/control.py", "exists": True, "anchors": []}],
             "suggestion": "先完成或标 blocked", "verify": "构造未闭合 finish"},
        ],
        "recommendations": [
            {"id": "R1", "priority": "P0", "title": "完成门：路线未闭合", "recommendation": "先完成或标 blocked",
             "target_files": [{"path": "phone_agent/v2/tools/control.py", "exists": True, "anchors": []}],
             "verification": "构造未闭合 finish"},
        ],
    }


def _evidence_fixture() -> list[dict[str, Any]]:
    return [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "买一杯咖啡",
         "config_digest": {"model_name": "m", "grounding_provider": "hybrid", "device_id": "dev1"}, "ts": 1.0},
        {"event": "model_response", "step": 1, "thinking": "先看看当前屏幕再决定点哪里",
         "tool_calls": [{"name": "read_screen", "args": {}}], "usage": None, "ts": 1.5},
        {"event": "tool_observation", "step": 1, "tool": "read_screen", "latency_ms": 5,
         "result_text": "[OBS] app=com.x screen#1\nmarks (3): a; b; c",
         "obs": {"current_app": "com.x", "screen_seq": 1, "mark_count": 3},
         "image": {"present": True, "screen_seq": 1, "bytes": 4096, "path": "screenshots/screen-1.png"}, "error": None, "ts": 2.0},
        {"event": "run_end", "steps": 4, "terminal": {"finished": False}, "ts": 3.0},
    ]


def test_render_is_base64_free_and_has_base_target():
    html = render_html(_summary_fixture(), _evidence_fixture())
    assert '<base target="_blank">' in html
    # no data: URL / long base64 run leaked in.
    assert "data:image" not in html
    assert "QUJD" * 20 not in html


def test_render_has_overview_blocks():
    html = render_html(_summary_fixture(), _evidence_fixture())
    assert "终局裁定" in html
    assert "任务板终态" in html
    assert "80/20 三件事" in html
    assert "renderTerminalBlock" in html
    assert "renderTaskBoardBlock" in html
    assert "renderTopThree" in html


def test_render_has_step_replay_with_screenshot_reference():
    html = render_html(_summary_fixture(), _evidence_fixture())
    # the replay tab + its renderer exist and the replay data is embedded.
    assert "逐步回放" in html
    assert "renderReplay" in html
    assert "renderStep" in html
    # the screenshot path from replay is embedded in the data island so the
    # client-side <img src="screenshots/..."> can render the real screenshot.
    assert "screenshots/screen-1.png" in html
    # model thinking full text is embedded (full fidelity, not truncated).
    assert "先看看当前屏幕再决定点哪里" in html


def test_render_embeds_token_usage_and_evidence_note():
    html = render_html(_summary_fixture(), _evidence_fixture())
    # token usage surfaces in the header/overview.
    assert "1540" in html
    # per-item evidence note is embedded for the completed route item.
    assert "已选中杯" in html


def test_render_embeds_summary_data():
    summary = _summary_fixture()
    html = render_html(summary, _evidence_fixture())
    assert "打开设置" in html
    assert "report-data" in html
    assert "application/json" in html


def test_script_close_sequence_is_escaped():
    summary = _summary_fixture()
    summary["target"] = "危险</script><script>alert(1)</script>"
    html = render_html(summary, [])
    assert html.count("</script>") == 2  # data island + app script
    assert "危险\\u003c" in html or "\\u003c/script\\u003e" in html


def test_escape_helper_neutralizes_angle_brackets():
    out = _escape_report_data('{"x": "</script>"}')
    assert "</script>" not in out
    assert "\\u003c" in out
