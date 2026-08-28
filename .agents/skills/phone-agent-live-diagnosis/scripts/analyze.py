"""Analyze a diagnostic evidence stream into the v2 ``summary.json`` structure.

Per ``outputs/design-council/ROUND2-D1.md`` §3. Consumes an
:class:`~evidence.EvidenceView` (and the ``RunResult``-like outcome the driver
captured) and produces the dimension blocks the R1 report renders, in first-page
order: terminal + taskdoc_final -> finish_gate -> stagnation -> context -> hitl
-> tool_health / grounding / visual -> model, then findings + recommendations.

Everything here is pure (no I/O, no device); the middleware already redacted the
evidence, so nothing re-reads secrets. Analysis only *classifies* recorded text
via :mod:`taxonomy` — it never re-runs a tool.
"""

from __future__ import annotations

from typing import Any

from evidence import EvidenceView, result_text_of
from sourcemap import V2_SOURCE_RULES, add_line_numbers
from taxonomy import (
    CATEGORY_OF,
    classify_result,
    category_of,
)

_OPEN_STATUSES = ("pending", "in_progress")


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (``pct`` in 0..100). Empty -> 0.0."""

    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(1, int(round(pct / 100.0 * len(ordered))))
    rank = min(rank, len(ordered))
    return float(ordered[rank - 1])


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------
def classify_verdict(outcome: dict[str, Any], view: EvidenceView) -> str:
    """Map outcome + evidence to ``success|failed|takeover|max_steps|uncertain``.

    Order (§3): takeover_reason -> takeover; finished -> success;
    reason==max_model_calls -> max_steps; any error event / rejected finish ->
    failed; else uncertain.
    """

    terminal = (view.run_end or {}).get("terminal", {}) if view.run_end else {}
    takeover_reason = outcome.get("takeover_reason") or terminal.get("takeover_reason")
    if takeover_reason:
        return "takeover"
    finished = bool(outcome.get("finished") or terminal.get("finished"))
    if finished:
        return "success"
    reason = str(outcome.get("reason") or "")
    if reason in {"token_budget_exhausted", "loop_fuse", "max_model_calls"}:
        return "max_steps"
    # An "error event" is a raised-exception tool result OR a fail-closed error
    # return string (same accounting as build_tool_health's _ERROR_CLASSES), so
    # the verdict never disagrees with a non-zero tool_health error count.
    has_error = any(
        c.get("error") or classify_result(c["result_text"]) in _ERROR_CLASSES
        for c in view.tool_calls
    )
    rejected_finish = any(
        classify_result(c["result_text"]) in {"finish_no_evidence", "finish_blocked_open_items"}
        for c in view.finish_calls()
    )
    if has_error or rejected_finish:
        return "failed"
    return "uncertain"


# ---------------------------------------------------------------------------
# terminal
# ---------------------------------------------------------------------------
def build_terminal(outcome: dict[str, Any], view: EvidenceView) -> dict[str, Any]:
    terminal = (view.run_end or {}).get("terminal", {}) if view.run_end else {}
    return {
        "finished": bool(outcome.get("finished") or terminal.get("finished")),
        "finish_summary": outcome.get("finish_summary") or terminal.get("finish_summary"),
        "takeover_reason": outcome.get("takeover_reason") or terminal.get("takeover_reason"),
        "reason": outcome.get("reason"),
        "returncode": outcome.get("returncode"),
    }


# ---------------------------------------------------------------------------
# finish_gate
# ---------------------------------------------------------------------------
def build_finish_gate(view: EvidenceView) -> dict[str, Any]:
    finish_calls = view.finish_calls()
    rejections: list[dict[str, Any]] = []
    accepted = False
    blocked_open = False
    open_items_at_finish: list[str] = []
    for call in finish_calls:
        cls = classify_result(call["result_text"])
        if cls == "finish_ok":
            accepted = True
        elif cls in {"finish_no_evidence", "finish_blocked_open_items"}:
            rejections.append(
                {
                    "step": call.get("step"),
                    "class": cls,
                    "message": call["result_text"],
                }
            )
            if cls == "finish_blocked_open_items":
                blocked_open = True
    # open items at the moment of a blocked finish: use the latest snapshot.
    if blocked_open:
        snap = view.latest_taskdoc()
        if snap:
            open_items_at_finish = [
                f"{it.get('id')}:{it.get('content')}"
                for it in snap.get("items", [])
                if it.get("status") in _OPEN_STATUSES
            ]
    return {
        "attempted": bool(finish_calls),
        "accepted": accepted,
        "blocked_by_open_items": blocked_open,
        "open_items_at_finish": open_items_at_finish,
        "rejections": rejections,
    }


# ---------------------------------------------------------------------------
# taskdoc_final
# ---------------------------------------------------------------------------
def build_taskdoc_final(view: EvidenceView) -> dict[str, Any]:
    snap = view.latest_taskdoc()
    if not snap:
        return {
            "goal_base": None,
            "amendments": [],
            "items": [],
            "facts": [],
            "counts": {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "blocked": 0,
            },
            "open_item_count": 0,
            "terminal_state": "no_board",
        }
    items = snap.get("items", []) or []
    counts = {"total": len(items), "completed": 0, "in_progress": 0, "pending": 0, "blocked": 0}
    for it in items:
        status = it.get("status")
        if status in counts:
            counts[status] += 1
    open_count = counts["pending"] + counts["in_progress"]
    if not items:
        terminal_state = "no_board"
    elif counts["blocked"]:
        terminal_state = "blocked_present"
    elif open_count:
        terminal_state = "has_open"
    else:
        terminal_state = "all_completed"
    return {
        "goal_base": snap.get("goal_base"),
        "amendments": snap.get("amendments", []),
        "items": items,
        "facts": snap.get("facts", []),
        "counts": counts,
        "open_item_count": open_count,
        "terminal_state": terminal_state,
    }


# ---------------------------------------------------------------------------
# stagnation
# ---------------------------------------------------------------------------
def build_stagnation(view: EvidenceView) -> dict[str, Any]:
    nudges = view.stagnation_nudges
    peak = 0
    for n in nudges:
        peak = max(peak, int(n.get("stagnant_steps", 0) or 0))
    # max distinct states = highest taskdoc-free proxy: count unique obs screens.
    seen_screens = set()
    for obs in view.observations:
        block = obs.get("obs") or {}
        seq = block.get("screen_seq")
        if seq is not None:
            seen_screens.add(seq)
    return {
        "nudged": bool(nudges),
        "nudge_step": nudges[0].get("step") if nudges else None,
        "max_seen_states": len(seen_screens),
        "stagnant_streak_peak": peak,
    }


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
def build_context(view: EvidenceView) -> dict[str, Any]:
    reqs = view.model_requests
    peak_msg = max((r.get("message_count", 0) for r in reqs), default=0)
    peak_img = max((r.get("image_message_count", 0) for r in reqs), default=0)
    pruned_total = sum(r.get("pruned_screen_count", 0) for r in reqs)
    pinned_every = bool(reqs) and all(r.get("taskdoc_present") for r in reqs)
    avg_chars = _avg([float(r.get("context_chars", 0)) for r in reqs])
    return {
        "peak_message_count": peak_msg,
        "peak_image_messages": peak_img,
        "pruned_screen_total": pruned_total,
        "taskdoc_pinned_every_step": pinned_every,
        "avg_context_chars": avg_chars,
    }


# ---------------------------------------------------------------------------
# hitl
# ---------------------------------------------------------------------------
def build_hitl(view: EvidenceView) -> dict[str, Any]:
    decisions = view.hitl_decisions
    approvals = sum(1 for d in decisions if d.get("decision") == "approve")
    rejections = sum(1 for d in decisions if d.get("decision") == "reject")
    responds = sum(1 for d in decisions if d.get("decision") == "respond")
    ask_user = sum(
        1 for c in view.tool_calls if classify_result(c["result_text"]) == "ask_user"
    )
    take_over = sum(
        1 for c in view.tool_calls if classify_result(c["result_text"]) == "takeover_requested"
    )
    return {
        "interrupts": len(decisions),
        "decisions": [
            {"step": d.get("step"), "tool": d.get("tool"), "decision": d.get("decision")}
            for d in decisions
        ],
        "approvals": approvals,
        "rejections": rejections,
        "responds": responds,
        "ask_user_count": ask_user,
        "take_over_count": take_over,
    }


# ---------------------------------------------------------------------------
# tool_health
# ---------------------------------------------------------------------------
def build_tool_health(view: EvidenceView) -> dict[str, Any]:
    by_tool: dict[str, dict[str, Any]] = {}
    total_calls = 0
    total_errors = 0
    for call in view.tool_calls:
        tool = call.get("tool") or "?"
        cls = classify_result(call["result_text"])
        is_error = bool(call.get("error")) or cls in _ERROR_CLASSES
        total_calls += 1
        if is_error:
            total_errors += 1
        stats = by_tool.setdefault(
            tool,
            {"calls": 0, "ok": 0, "error": 0, "error_classes": {}, "_latencies": []},
        )
        stats["calls"] += 1
        if is_error:
            stats["error"] += 1
            stats["error_classes"][cls] = stats["error_classes"].get(cls, 0) + 1
        else:
            stats["ok"] += 1
        latency = call.get("latency_ms")
        if isinstance(latency, (int, float)):
            stats["_latencies"].append(float(latency))
    for tool, stats in by_tool.items():
        lat = stats.pop("_latencies")
        stats["avg_latency_ms"] = _avg(lat)
        stats["p95_latency_ms"] = round(_percentile(lat, 95), 2)
    return {
        "total_calls": total_calls,
        "total_errors": total_errors,
        "error_rate": round(total_errors / total_calls, 3) if total_calls else 0.0,
        "by_tool": by_tool,
    }


# Classes that count as a tool error for health/verdict purposes.
_ERROR_CLASSES = {
    "obs_capture_failed",
    "addressing_conflict",
    "addressing_missing",
    "stale_mark",
    "ambiguous_resolve",
    "locate_no_match",
    "locate_provider_error",
    "bad_coords",
    "bad_direction",
    "ambiguous_app",
    "launch_denied",
    "app_not_installed",
    "unknown_app",
    "taskdoc_input_invalid",
    "taskdoc_validation_failed",
    "finish_no_evidence",
    "finish_blocked_open_items",
}


# ---------------------------------------------------------------------------
# grounding
# ---------------------------------------------------------------------------
def build_grounding(view: EvidenceView) -> dict[str, Any]:
    by_mark_id = 0
    by_description = 0
    ambiguous = 0
    stale = 0
    no_match = 0
    locate_calls = 0
    locate_success = 0
    locate_no_match = 0
    locate_provider_error = 0
    launch = {"resolved": 0, "denied": 0, "unknown": 0, "not_installed": 0, "ambiguous": 0}

    for call in view.tool_calls:
        tool = call.get("tool")
        invoke = call.get("invoke") or {}
        args = invoke.get("args") if isinstance(invoke, dict) else None
        args = args if isinstance(args, dict) else {}
        cls = classify_result(call["result_text"])

        if tool in {"tap", "long_press", "type_text"}:
            if args.get("target_mark_id"):
                by_mark_id += 1
            elif args.get("target_description"):
                by_description += 1

        if cls == "ambiguous_resolve":
            ambiguous += 1
        elif cls == "stale_mark":
            stale += 1
        elif cls == "locate_no_match":
            no_match += 1

        if tool == "locate":
            locate_calls += 1
            if cls == "locate_no_match":
                locate_no_match += 1
            elif cls == "locate_provider_error":
                locate_provider_error += 1
            else:
                locate_success += 1

        if tool == "launch_app":
            if cls == "success":
                launch["resolved"] += 1
            elif cls == "launch_denied":
                launch["denied"] += 1
            elif cls == "app_not_installed":
                launch["not_installed"] += 1
            elif cls == "ambiguous_app":
                launch["ambiguous"] += 1
            elif cls == "unknown_app":
                launch["unknown"] += 1

    return {
        "mark_addressing": {"by_mark_id": by_mark_id, "by_description": by_description},
        "resolve_failures": {"ambiguous": ambiguous, "stale": stale, "no_match": no_match},
        "locate": {
            "calls": locate_calls,
            "success": locate_success,
            "no_match": locate_no_match,
            "provider_error": locate_provider_error,
        },
        "launch": launch,
    }


# ---------------------------------------------------------------------------
# visual (D2)
# ---------------------------------------------------------------------------
def build_visual(view: EvidenceView) -> dict[str, Any]:
    with_image = 0
    total_bytes = 0
    first_step: int | None = None
    last_step: int | None = None
    for obs in view.observations:
        image = obs.get("image") or {}
        if image.get("present"):
            with_image += 1
            total_bytes += int(image.get("bytes", 0) or 0)
            step = obs.get("step")
            if first_step is None:
                first_step = step
            last_step = step
    return {
        "tool_results_with_image": with_image,
        "total_image_bytes": total_bytes,
        "first_image_step": first_step,
        "last_image_step": last_step,
    }


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def build_model(view: EvidenceView) -> dict[str, Any]:
    # Model latency is not recorded by the diagnostic stream (that's the trace's
    # model_call event); calls are counted from model_request events.
    return {
        "calls": len(view.model_requests),
        "avg_latency_ms": None,
        "p95_latency_ms": None,
        "errors": 0,
    }


# ---------------------------------------------------------------------------
# findings + recommendations
# ---------------------------------------------------------------------------
def build_findings(view: EvidenceView) -> list[dict[str, Any]]:
    """One finding per report category that fired an error/notable class."""

    cat_counts: dict[str, int] = {}
    cat_examples: dict[str, list[str]] = {}
    for call in view.tool_calls:
        cls = classify_result(call["result_text"])
        if cls in _ERROR_CLASSES:
            cat = category_of(cls)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            cat_examples.setdefault(cat, [])
            if len(cat_examples[cat]) < 3:
                cat_examples[cat].append(call["result_text"][:200])

    findings: list[dict[str, Any]] = []
    for cat, count in sorted(cat_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        rule = V2_SOURCE_RULES.get(cat)
        if not rule:
            continue
        findings.append(
            {
                "category": cat,
                "layer": rule["layer"],
                "severity": rule["severity"],
                "title": rule["title"],
                "count": count,
                "examples": cat_examples.get(cat, []),
                "files": add_line_numbers(rule["files"]),
                "suggestion": rule["suggestion"],
                "verify": rule["verify"],
            }
        )
    return findings


def build_summary(
    outcome: dict[str, Any],
    view: EvidenceView,
    *,
    run_id: str,
    created_at: str,
    target: str,
    run_dir: str | None = None,
    command: list[str] | None = None,
    duration_sec: float | None = None,
    evidence_stream: str | None = None,
    trace: str | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full v2 ``summary.json`` dict (§3) from outcome + evidence.

    ``target`` prefers the already-redacted ``task_goal_base`` from the evidence
    ``run_start`` header (so we never re-introduce an unredacted goal), falling
    back to the caller-supplied value.
    """

    verdict = classify_verdict(outcome, view)
    findings = build_findings(view)
    recommendations = build_recommendations(findings, view, verdict)
    steps = None
    if view.run_end:
        steps = view.run_end.get("steps")
    if steps is None:
        steps = outcome.get("steps")
    redacted_target = (view.run_start or {}).get("task_goal_base") or target
    return {
        "run_id": run_id,
        "created_at": created_at,
        "target": redacted_target,
        "verdict": verdict,
        "run_dir": run_dir,
        "command": command or [],
        "duration_sec": duration_sec,
        "steps": steps,
        "evidence_stream": evidence_stream,
        "trace": trace,
        "artifacts": artifacts or {},
        "terminal": build_terminal(outcome, view),
        "finish_gate": build_finish_gate(view),
        "taskdoc_final": build_taskdoc_final(view),
        "stagnation": build_stagnation(view),
        "context": build_context(view),
        "hitl": build_hitl(view),
        "tool_health": build_tool_health(view),
        "grounding": build_grounding(view),
        "visual": build_visual(view),
        "model": build_model(view),
        "findings": findings,
        "recommendations": recommendations,
    }


def build_recommendations(
    findings: list[dict[str, Any]], view: EvidenceView, verdict: str
) -> list[dict[str, Any]]:
    """80/20 recommendations: the top findings + verdict-driven guidance."""

    recs: list[dict[str, Any]] = []
    for idx, f in enumerate(findings[:5], start=1):
        recs.append(
            {
                "id": f"R{idx}",
                "priority": f["severity"],
                "title": f["title"],
                "recommendation": f["suggestion"],
                "target_files": f["files"],
                "verification": f["verify"],
            }
        )
    # verdict-specific top-of-list guidance.
    visual = build_visual(view)
    if visual["tool_results_with_image"] == 0 and view.observations:
        recs.insert(
            0,
            {
                "id": "R0",
                "priority": "P0",
                "title": "视觉回流断供：工具返回未携带截图",
                "recommendation": (
                    "tool_results_with_image=0——模型在纯文本 marks 摘要上盲操作。核对 "
                    "_obs.py / actuation.py 是否把截图 image 块随工具返回回流。"
                ),
                "target_files": add_line_numbers(V2_SOURCE_RULES["visual"]["files"]),
                "verification": V2_SOURCE_RULES["visual"]["verify"],
            },
        )
    return recs


__all__ = [
    "classify_verdict",
    "build_terminal",
    "build_finish_gate",
    "build_taskdoc_final",
    "build_stagnation",
    "build_context",
    "build_hitl",
    "build_tool_health",
    "build_grounding",
    "build_visual",
    "build_model",
    "build_findings",
    "build_recommendations",
    "build_summary",
]
