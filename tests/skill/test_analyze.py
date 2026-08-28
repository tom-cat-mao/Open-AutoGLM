"""Analyzer tests: synthetic evidence -> summary.json dimensions (§5.3).

Feeds hand-built evidence event lists (the exact shape
``DiagnosticEvidenceMiddleware`` writes) through :class:`EvidenceView` +
:func:`build_summary` and asserts the dimension blocks the R1 report relies on:
finish-gate blocked-by-open-items, taskdoc terminal state, tool-health error
classes, HITL decisions, grounding tallies, visual reflow, and the verdict.
No middleware, no device — pure analysis over recorded (already-redacted) text.
"""

from __future__ import annotations

from typing import Any

from analyze import build_summary, classify_verdict
from evidence import EvidenceView


def _view(events: list[dict[str, Any]]) -> EvidenceView:
    return EvidenceView.from_events(events)


def _summary(events: list[dict[str, Any]], outcome: dict[str, Any]) -> dict[str, Any]:
    return build_summary(
        outcome,
        _view(events),
        run_id="t1",
        created_at="2026-01-01T00:00:00",
        target="打开设置",
    )


# --------------------------------------------------------------------------
# scenario A: finish blocked by open route items -> failed verdict
# --------------------------------------------------------------------------
def _blocked_finish_events() -> list[dict[str, Any]]:
    return [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "买一杯咖啡", "config_digest": {}},
        {"event": "model_request", "step": 1, "message_count": 3, "image_message_count": 1,
         "pruned_screen_count": 0, "taskdoc_present": True, "taskdoc_open_items": 2, "context_chars": 500},
        {"event": "taskdoc_snapshot", "step": 1, "goal_base": "买一杯咖啡", "amendments": [],
         "items": [
             {"id": "s1", "content": "选规格", "status": "completed", "reason": None},
             {"id": "s2", "content": "付款", "status": "pending", "reason": None},
         ], "facts": [], "open_item_count": 1},
        {"event": "tool_invoke", "step": 2, "tool": "finish", "args": {"summary": "done", "evidence": ["x"]}},
        {"event": "tool_observation", "step": 2, "tool": "finish", "latency_ms": 3,
         "result_text": "路线仍有未完成项：s2:付款[pending]。请先完成", "obs": None,
         "image": {"present": False, "screen_seq": None, "bytes": 0}, "error": None},
        {"event": "run_end", "steps": 2, "terminal": {"finished": False, "takeover_reason": None, "finish_summary": None}},
    ]


def test_finish_gate_blocked_by_open_items():
    events = _blocked_finish_events()
    summary = _summary(events, {"finished": False, "reason": "model_stopped"})
    fg = summary["finish_gate"]
    assert fg["attempted"] is True
    assert fg["accepted"] is False
    assert fg["blocked_by_open_items"] is True
    assert fg["open_items_at_finish"] == ["s2:付款"]
    assert fg["rejections"][0]["class"] == "finish_blocked_open_items"
    # taskdoc terminal state has an open item.
    assert summary["taskdoc_final"]["terminal_state"] == "has_open"
    assert summary["taskdoc_final"]["counts"]["completed"] == 1
    # a rejected finish makes the verdict failed.
    assert summary["verdict"] == "failed"


# --------------------------------------------------------------------------
# scenario B: ambiguous resolve + bad direction -> tool-health error classes
# --------------------------------------------------------------------------
def _grounding_error_events() -> list[dict[str, Any]]:
    return [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "点登录", "config_digest": {}},
        {"event": "model_request", "step": 1, "message_count": 2, "image_message_count": 1,
         "pruned_screen_count": 0, "taskdoc_present": False, "taskdoc_open_items": 0, "context_chars": 200},
        {"event": "tool_invoke", "step": 1, "tool": "tap", "args": {"target_description": "登录"}},
        {"event": "tool_observation", "step": 1, "tool": "tap", "latency_ms": 12,
         "result_text": "ambiguous: 登录; 登陆 — refine the description", "obs": None,
         "image": {"present": False, "screen_seq": None, "bytes": 0}, "error": None},
        {"event": "tool_invoke", "step": 2, "tool": "scroll", "args": {"direction": "sideways"}},
        {"event": "tool_observation", "step": 2, "tool": "scroll", "latency_ms": 4,
         "result_text": "error: unknown direction 'sideways'; use up|down", "obs": None,
         "image": {"present": False, "screen_seq": None, "bytes": 0}, "error": None},
        {"event": "tool_invoke", "step": 3, "tool": "tap", "args": {"target_mark_id": "ax_5"}},
        {"event": "tool_observation", "step": 3, "tool": "tap", "latency_ms": 8,
         "result_text": "OK. tap 登录 at (100,200)\n[OBS] app=x screen#2\nmarks (3): a",
         "obs": {"current_app": "x", "screen_seq": 2, "mark_count": 3},
         "image": {"present": True, "screen_seq": 2, "bytes": 4096}, "error": None},
        {"event": "run_end", "steps": 3, "terminal": {"finished": False, "takeover_reason": None, "finish_summary": None}},
    ]


def test_tool_health_and_grounding_and_visual():
    events = _grounding_error_events()
    summary = _summary(events, {"finished": False, "reason": "model_stopped"})

    th = summary["tool_health"]
    assert th["total_calls"] == 3
    assert th["total_errors"] == 2
    assert th["by_tool"]["tap"]["error_classes"] == {"ambiguous_resolve": 1}
    assert th["by_tool"]["scroll"]["error_classes"] == {"bad_direction": 1}
    assert th["by_tool"]["tap"]["ok"] == 1

    g = summary["grounding"]
    assert g["mark_addressing"]["by_mark_id"] == 1
    assert g["mark_addressing"]["by_description"] == 1
    assert g["resolve_failures"]["ambiguous"] == 1

    v = summary["visual"]
    assert v["tool_results_with_image"] == 1
    assert v["total_image_bytes"] == 4096
    assert v["first_image_step"] == 3

    # an error event with no finish/takeover -> failed.
    assert summary["verdict"] == "failed"
    # findings point grounding at its v2 source files.
    cats = {f["category"] for f in summary["findings"]}
    assert "grounding_addressing" in cats
    assert "actuation_arg" in cats


# --------------------------------------------------------------------------
# scenario C: take-over -> takeover verdict + HITL decisions
# --------------------------------------------------------------------------
def _takeover_events() -> list[dict[str, Any]]:
    return [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "登录", "config_digest": {}},
        {"event": "model_request", "step": 1, "message_count": 2, "image_message_count": 1,
         "pruned_screen_count": 0, "taskdoc_present": True, "taskdoc_open_items": 1, "context_chars": 300},
        {"event": "tool_invoke", "step": 1, "tool": "take_over", "args": {"reason": "需要验证码"}},
        {"event": "tool_observation", "step": 1, "tool": "take_over", "latency_ms": 1,
         "result_text": "已请求人工接管: 需要验证码", "obs": None,
         "image": {"present": False, "screen_seq": None, "bytes": 0}, "error": None},
        {"event": "hitl_decision", "step": 1, "tool": "take_over",
         "requested_action": "需要验证码", "decision": "respond", "response_text": "已处理"},
        {"event": "run_end", "steps": 1,
         "terminal": {"finished": False, "takeover_reason": "需要验证码", "finish_summary": None}},
    ]


def test_takeover_verdict_and_hitl():
    events = _takeover_events()
    view = _view(events)
    outcome = {"finished": False, "takeover_reason": "需要验证码", "reason": ""}
    assert classify_verdict(outcome, view) == "takeover"

    summary = build_summary(outcome, view, run_id="t1", created_at="t", target="登录")
    assert summary["verdict"] == "takeover"
    assert summary["terminal"]["takeover_reason"] == "需要验证码"
    hitl = summary["hitl"]
    assert hitl["interrupts"] == 1
    assert hitl["responds"] == 1
    assert hitl["take_over_count"] == 1
    assert hitl["decisions"][0]["decision"] == "respond"


# --------------------------------------------------------------------------
# scenario D: clean success + target prefers redacted evidence goal_base
# --------------------------------------------------------------------------
def test_success_verdict_and_redacted_target_wins():
    events = [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "给 <redacted> 发消息", "config_digest": {}},
        {"event": "model_request", "step": 1, "message_count": 2, "image_message_count": 1,
         "pruned_screen_count": 0, "taskdoc_present": True, "taskdoc_open_items": 0, "context_chars": 100},
        {"event": "taskdoc_snapshot", "step": 1, "goal_base": "给 <redacted> 发消息", "amendments": [],
         "items": [{"id": "s1", "content": "发送", "status": "completed", "reason": None}],
         "facts": [], "open_item_count": 0},
        {"event": "tool_invoke", "step": 2, "tool": "finish", "args": {}},
        {"event": "tool_observation", "step": 2, "tool": "finish", "latency_ms": 2,
         "result_text": "已记录完成声明", "obs": None,
         "image": {"present": False, "screen_seq": None, "bytes": 0}, "error": None},
        {"event": "run_end", "steps": 2, "terminal": {"finished": True, "takeover_reason": None, "finish_summary": "完成"}},
    ]
    # caller passes an UNREDACTED target; build_summary must prefer the redacted
    # goal_base recorded in run_start so the report never re-introduces a secret.
    summary = _summary(events, {"finished": True, "reason": "finished"})
    assert summary["verdict"] == "success"
    assert summary["target"] == "给 <redacted> 发消息"
    assert summary["finish_gate"]["accepted"] is True
    assert summary["taskdoc_final"]["terminal_state"] == "all_completed"


def test_no_board_and_max_steps():
    events = [
        {"event": "run_start", "run_id": "t1", "task_goal_base": "x", "config_digest": {}},
        {"event": "model_request", "step": 1, "message_count": 1, "image_message_count": 0,
         "pruned_screen_count": 0, "taskdoc_present": False, "taskdoc_open_items": 0, "context_chars": 10},
        {"event": "run_end", "steps": 20, "terminal": {"finished": False}},
    ]
    summary = _summary(events, {"finished": False, "reason": "max_model_calls"})
    assert summary["verdict"] == "max_steps"
    assert summary["taskdoc_final"]["terminal_state"] == "no_board"
