"""Analyze a diagnostic evidence stream into the v2 ``summary.json`` structure.

Per ``outputs/design-council/ROUND2-D1.md`` §3. Consumes an
:class:`~evidence.EvidenceView` (and the ``RunResult``-like outcome the driver
captured) and produces the dimension blocks the R1 report renders, in first-page
order: terminal + taskdoc_final -> finish_gate -> stagnation -> context -> hitl
-> tool_health / grounding / visual -> model, then findings + recommendations.

Analysis never touches the device or re-runs a tool.  In addition to the
diagnostic evidence stream it best-effort reads the run's production trace and
privacy-minimal memory ledgers.  Those optional inputs are deliberately
fail-open: absent, malformed, or partially-written files produce empty
dimension blocks without changing the established summary fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from evidence import EvidenceView, result_text_of
from sourcemap import V2_SOURCE_RULES, add_line_numbers
from taxonomy import (
    classify_result,
    category_of,
)

_OPEN_STATUSES = ("pending", "in_progress")
_RESOLVER_ROUTES = ("exact", "lexical", "pinyin", "embedding")
_LAUNCHED_PACKAGE_RE = re.compile(r"\blaunched\s+.+?\s+\(([A-Za-z][A-Za-z0-9_.]+)\)")
_RUN_ID_IN_NOTE_RE = re.compile(r"run<([^<>]+)>")
_REPO_ROOT = Path(__file__).resolve().parents[4]


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
        classify_result(c["result_text"])
        in {"finish_no_evidence", "finish_blocked_open_items"}
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
        "finish_summary": outcome.get("finish_summary")
        or terminal.get("finish_summary"),
        "takeover_reason": outcome.get("takeover_reason")
        or terminal.get("takeover_reason"),
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
    counts = {
        "total": len(items),
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "blocked": 0,
    }
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
        1
        for c in view.tool_calls
        if classify_result(c["result_text"]) == "takeover_requested"
    )
    return {
        "interrupts": len(decisions),
        "decisions": [
            {
                "step": d.get("step"),
                "tool": d.get("tool"),
                "decision": d.get("decision"),
            }
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
    "launch_failed",
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
    launch = {
        "resolved": 0,
        "denied": 0,
        "unknown": 0,
        "not_installed": 0,
        "ambiguous": 0,
        "failed": 0,
    }

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
            elif cls == "launch_failed":
                launch["failed"] += 1
            elif cls == "ambiguous_app":
                launch["ambiguous"] += 1
            elif cls == "unknown_app":
                launch["unknown"] += 1

    return {
        "mark_addressing": {"by_mark_id": by_mark_id, "by_description": by_description},
        "resolve_failures": {
            "ambiguous": ambiguous,
            "stale": stale,
            "no_match": no_match,
        },
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
    # model_call event); calls are counted from model_request events. Token usage
    # is aggregated from model_response events when the model reports it.
    prompt_tokens = 0
    output_tokens = 0
    total_tokens = 0
    have_usage = False
    for resp in view.model_responses:
        usage = resp.get("usage")
        if not isinstance(usage, dict):
            continue
        have_usage = True
        prompt_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    if have_usage and not total_tokens:
        total_tokens = prompt_tokens + output_tokens
    return {
        "calls": len(view.model_requests),
        "avg_latency_ms": None,
        "p95_latency_ms": None,
        "errors": 0,
        "token_usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        if have_usage
        else None,
    }


# ---------------------------------------------------------------------------
# resolver / memory / capabilities (WP-S2, optional production artifacts)
# ---------------------------------------------------------------------------
def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    """Best-effort JSONL reader for partially-written observability files."""

    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _read_jsonl_tail(
    path: Path | None, *, max_lines: int = 5000
) -> list[dict[str, Any]]:
    """Read a bounded JSONL tail while tolerating missing and bad lines."""

    if path is None:
        return []
    try:
        with path.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=max_lines)
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_root(run_dir: str | None, evidence_stream: str | None) -> Path | None:
    if run_dir:
        return Path(run_dir).expanduser()
    if evidence_stream:
        return Path(evidence_stream).expanduser().parent
    return None


def _trace_file(
    trace: str | None, root: Path | None, source_run_id: str
) -> Path | None:
    candidates: list[Path] = []
    if trace:
        candidates.append(Path(trace).expanduser())
    if root is not None:
        candidates.append(root / "traces")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
            if not candidate.is_dir():
                continue
            preferred = candidate / f"{source_run_id}.jsonl"
            if preferred.is_file():
                return preferred
            matches = sorted(candidate.glob("*.jsonl"))
        except OSError:
            matches = []
        if len(matches) == 1:
            return matches[0]
    return None


def _text_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                texts.append(item)
        return texts
    if isinstance(value, dict):
        return [str(value.get("text", ""))] if "text" in value else []
    return []


def _launched_package(value: Any) -> str | None:
    for text in _text_fragments(value):
        if not text.lstrip().startswith("OK."):
            continue
        match = _LAUNCHED_PACKAGE_RE.search(text)
        if match:
            return match.group(1)
    return None


def _launch_succeeded(value: Any) -> bool:
    return any(
        text.lstrip().startswith("OK.") and "launched" in text
        for text in _text_fragments(value)
    )


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_resolver(trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract app-name resolution attempts and route quality from trace."""

    attempts: list[dict[str, Any]] = []
    pending: int | None = None
    launch_step: Any = None
    for event in trace_events:
        event_name = event.get("event")
        if event_name == "tool_call" and event.get("tool") == "launch_app":
            launch_step = event.get("step")
            pending = None
            continue
        if event_name == "resolution_attempt":
            raw_candidates = event.get("candidates")
            candidates = [
                dict(item)
                for item in (raw_candidates if isinstance(raw_candidates, list) else [])
                if isinstance(item, dict)
            ]
            top1 = candidates[0] if candidates else {}
            first_score = _number(top1.get("score"))
            second_score = (
                _number(candidates[1].get("score")) if len(candidates) > 1 else None
            )
            margin = (
                round(first_score - second_score, 6)
                if first_score is not None and second_score is not None
                else None
            )
            attempt = {
                "launch_index": len(attempts) + 1,
                "step": event.get("step", launch_step),
                "mention": str(event.get("mention", "")),
                "route": top1.get("source_route"),
                "top1_package": top1.get("package"),
                "top1_score": first_score,
                "margin": margin,
                "decision": str(event.get("decision", "unknown")),
                "winner": event.get("winner"),
                "candidate_count": len(candidates),
                "candidates": candidates,
                "launch_succeeded": False,
                "launched_package": None,
            }
            attempts.append(attempt)
            pending = len(attempts) - 1
            continue
        if (
            event_name == "tool_result"
            and event.get("tool") == "launch_app"
            and pending is not None
        ):
            package = _launched_package(event.get("result"))
            succeeded = _launch_succeeded(event.get("result"))
            if succeeded and package is None:
                package = attempts[pending].get("winner")
            attempts[pending]["step"] = event.get("step", attempts[pending]["step"])
            attempts[pending]["launch_succeeded"] = succeeded
            attempts[pending]["launched_package"] = package
            pending = None

    route_stats = {
        route: {
            "attempts": 0,
            "resolved": 0,
            "successful_launches": 0,
            "resolution_rate": 0.0,
            "launch_success_rate": 0.0,
        }
        for route in _RESOLVER_ROUTES
    }
    decision_counts = {"resolved": 0, "ambiguous": 0, "unknown": 0}
    for attempt in attempts:
        decision = attempt["decision"]
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        route = attempt.get("route")
        if route not in route_stats:
            continue
        stats = route_stats[route]
        stats["attempts"] += 1
        stats["resolved"] += int(decision == "resolved")
        stats["successful_launches"] += int(attempt["launch_succeeded"])
    for stats in route_stats.values():
        count = stats["attempts"]
        if count:
            stats["resolution_rate"] = round(stats["resolved"] / count, 3)
            stats["launch_success_rate"] = round(
                stats["successful_launches"] / count, 3
            )

    recoveries: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts):
        if attempt["decision"] != "ambiguous":
            continue
        candidate_packages = {
            str(item.get("package"))
            for item in attempt["candidates"]
            if item.get("package")
        }
        recovery = next(
            (
                later
                for later in attempts[index + 1 :]
                if later["decision"] == "resolved"
                and later["launch_succeeded"]
                and later.get("winner") in candidate_packages
            ),
            None,
        )
        if recovery is not None:
            recoveries.append(
                {
                    "ambiguous_launch_index": attempt["launch_index"],
                    "ambiguous_mention": attempt["mention"],
                    "recovery_launch_index": recovery["launch_index"],
                    "recovery_mention": recovery["mention"],
                    "winner": recovery["winner"],
                }
            )

    return {
        "attempts": attempts,
        "total_attempts": len(attempts),
        "decision_counts": decision_counts,
        "route_stats": route_stats,
        "ambiguous_count": decision_counts.get("ambiguous", 0),
        "embedding_launch_hits": [
            attempt
            for attempt in attempts
            if attempt.get("route") == "embedding" and attempt.get("launch_succeeded")
        ],
        "ambiguous_recoveries": recoveries,
    }


def _configured_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else _REPO_ROOT / candidate


def _memory_roots(root: Path | None, explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(_configured_path(explicit))
    elif os.getenv("PHONE_AGENT_MEMORY_DIR"):
        candidates.append(_configured_path(os.environ["PHONE_AGENT_MEMORY_DIR"]))
    if root is not None:
        candidates.extend((root / "memory", root))
    candidates.append(_REPO_ROOT / "memory")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _episode_for_run(roots: list[Path], run_id: str) -> dict[str, Any]:
    configured = os.getenv("PHONE_AGENT_EXPERIENCE_DIR")
    event_paths: list[Path] = []
    json_paths: list[Path] = []
    if configured:
        experience = _configured_path(configured)
        event_paths.append(experience / "events.jsonl")
        json_paths.append(experience / "episodes.json")
    for root in roots:
        event_paths.extend(
            (root / "experience/events.jsonl", root / "memory/experience/events.jsonl")
        )
        json_paths.extend(
            (
                root / "experience/episodes.json",
                root / "memory/experience/episodes.json",
            )
        )
    for event in reversed(_read_jsonl(_first_existing(event_paths))):
        if (
            event.get("type") == "episode_outcome"
            and str(event.get("run_id")) == run_id
        ):
            return event
    materialized = _read_json_object(_first_existing(json_paths))
    value = materialized.get(run_id)
    return dict(value) if isinstance(value, dict) else {}


def _alias_ref_id(entry: dict[str, Any]) -> str:
    identity = "\0".join(
        str(entry.get(key, "")) for key in ("term", "package", "kind", "scope")
    )
    return "alias:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _candidate_packages(
    candidate: dict[str, Any],
    *,
    episodes: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> list[str]:
    packages: list[str] = []
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    values = [
        candidate.get("package"),
        candidate.get("app_package"),
        metadata.get("app_package"),
    ]
    for value in values:
        if value:
            packages.append(str(value))
    for source in (candidate.get("apps"), metadata.get("apps")):
        if isinstance(source, list):
            packages.extend(str(value) for value in source if value)
    ref_id = str(candidate.get("ref_id", ""))
    if candidate.get("namespace") == "episode" and ref_id in episodes:
        packages.extend(
            str(value) for value in episodes[ref_id].get("apps", []) if value
        )
    if candidate.get("namespace") == "app_alias" and ref_id in aliases:
        packages.append(aliases[ref_id])
    if ref_id.startswith("registry:"):
        try:
            from phone_agent.config.apps import DEFAULT_APP_REGISTRY

            canonical_id = ref_id.removeprefix("registry:")
            identity = next(
                (
                    item
                    for item in DEFAULT_APP_REGISTRY.identities
                    if item.canonical_id == canonical_id
                ),
                None,
            )
            if identity is not None:
                packages.append(identity.primary_package)
        except Exception:  # noqa: BLE001 - optional package enrichment
            pass
    if ref_id.startswith("package:"):
        packages.append(ref_id.removeprefix("package:"))
    return list(dict.fromkeys(package for package in packages if package))


def _event_matches_run(
    event: dict[str, Any],
    run_ids: set[str],
    *,
    episode: dict[str, Any],
) -> bool:
    direct = {str(event.get(key, "")) for key in ("run_id", "evidence_run_id")}
    if direct & run_ids:
        return True
    note = str(event.get("evidence_note", ""))
    match = _RUN_ID_IN_NOTE_RE.search(note)
    if match and match.group(1) in run_ids:
        return True
    # Several App-KB writes predate an explicit run-id field. They can still be
    # attributed when their timestamp falls inside this episode's persisted
    # start/end window; no window means no attribution.
    try:
        stamp = datetime.fromisoformat(
            str(event["ts"]).replace("Z", "+00:00")
        ).timestamp()
        return float(episode["ts_start"]) <= stamp <= float(episode["ts_end"])
    except (KeyError, TypeError, ValueError):
        return False


def _clean_alias_event(event: dict[str, Any]) -> dict[str, Any]:
    entry = event.get("entry") if isinstance(event.get("entry"), dict) else {}
    return {
        "op": event.get("op"),
        "kind": entry.get("kind"),
        "term": entry.get("term", event.get("term")),
        "package": entry.get("package", event.get("package")),
        "old_package": event.get("old_package"),
        "new_package": event.get("new_package"),
        "changed": event.get("changed"),
        "ts": event.get("ts"),
    }


def build_memory(
    trace_events: list[dict[str, Any]],
    view: EvidenceView,
    *,
    source_run_id: str,
    summary_run_id: str,
    root: Path | None,
    memory_dir: str | None = None,
) -> dict[str, Any]:
    """Join run-start recall, confirmed launches, App-KB events, and episode."""

    roots = _memory_roots(root, memory_dir)
    episode = _episode_for_run(roots, source_run_id)

    appkb_paths: list[Path] = []
    for memory_root in roots:
        appkb_paths.extend(
            (
                memory_root / "app_kb/events.jsonl",
                memory_root / "memory/app_kb/events.jsonl",
            )
        )
    appkb_events = _read_jsonl_tail(_first_existing(appkb_paths))
    alias_packages: dict[str, str] = {}
    for event in appkb_events:
        entry = event.get("entry")
        if isinstance(entry, dict) and entry.get("package"):
            alias_packages[_alias_ref_id(entry)] = str(entry["package"])

    episode_events: dict[str, dict[str, Any]] = {}
    experience_paths: list[Path] = []
    configured_experience = os.getenv("PHONE_AGENT_EXPERIENCE_DIR")
    if configured_experience:
        experience_paths.append(
            _configured_path(configured_experience) / "events.jsonl"
        )
    for memory_root in roots:
        experience_paths.extend(
            (
                memory_root / "experience/events.jsonl",
                memory_root / "memory/experience/events.jsonl",
            )
        )
    for event in _read_jsonl(_first_existing(experience_paths)):
        if event.get("type") == "episode_outcome" and event.get("run_id"):
            episode_events[str(event["run_id"])] = event
    episode_json_paths: list[Path] = []
    if configured_experience:
        episode_json_paths.append(
            _configured_path(configured_experience) / "episodes.json"
        )
    for memory_root in roots:
        episode_json_paths.extend(
            (
                memory_root / "experience/episodes.json",
                memory_root / "memory/experience/episodes.json",
            )
        )
    for key, value in _read_json_object(_first_existing(episode_json_paths)).items():
        if isinstance(value, dict):
            episode_events.setdefault(str(key), value)

    actual_packages: list[str] = []
    for event in trace_events:
        if event.get("event") == "tool_result" and event.get("tool") == "launch_app":
            package = _launched_package(event.get("result"))
            if package:
                actual_packages.append(package)
    recall_evaluation: dict[str, Any] = {}
    for event in trace_events:
        if event.get("event") == "recall_evaluation" and isinstance(
            event.get("evaluation"), dict
        ):
            recall_evaluation = dict(event["evaluation"])
    actual_packages.extend(
        str(value) for value in recall_evaluation.get("actual_apps", []) if value
    )
    for call in view.tool_calls:
        if call.get("tool") == "launch_app":
            package = _launched_package(call.get("result_text"))
            if package:
                actual_packages.append(package)
    actual_packages.extend(str(value) for value in episode.get("apps", []) if value)
    actual_packages = list(dict.fromkeys(actual_packages))
    actual_set = set(actual_packages)

    recall_payload: dict[str, Any] = {}
    for event in trace_events:
        if event.get("event") == "run_start" and isinstance(
            event.get("memory_rag"), dict
        ):
            recall_payload = dict(event["memory_rag"])
            break
    candidates: list[dict[str, Any]] = []
    raw_candidates = recall_payload.get("candidates")
    for position, raw in enumerate(
        raw_candidates if isinstance(raw_candidates, list) else [], start=1
    ):
        if not isinstance(raw, dict):
            continue
        packages = _candidate_packages(
            raw, episodes=episode_events, aliases=alias_packages
        )
        matched = sorted(set(packages) & actual_set)
        candidates.append(
            {
                "rank": position,
                "namespace": raw.get("namespace"),
                "ref_id": raw.get("ref_id"),
                "score": _number(raw.get("score")),
                "packages": packages,
                "matched_packages": matched,
                "hit": bool(matched),
            }
        )
    matched_packages = sorted(
        {
            package
            for candidate in candidates
            for package in candidate["matched_packages"]
        }
    )
    matched_packages = sorted(
        set(matched_packages)
        | {str(value) for value in recall_evaluation.get("matched_apps", []) if value}
    )

    run_ids = {source_run_id, summary_run_id}
    alias_events = [
        _clean_alias_event(event)
        for event in appkb_events
        if _event_matches_run(event, run_ids, episode=episode)
        and (
            event.get("op") in {"alias_overwritten", "alias_user_set"}
            or (
                event.get("op") == "upsert"
                and isinstance(event.get("entry"), dict)
                and event["entry"].get("kind") in {"learned", "user"}
            )
        )
    ]

    return {
        "source_run_id": source_run_id,
        "memory_rag": {
            "mode": recall_payload.get("mode"),
            "status": recall_payload.get("status"),
            "candidates": candidates,
            "candidate_count": len(candidates),
            "actual_launch_packages": actual_packages,
            "matched_packages": matched_packages,
            "hit": bool(matched_packages),
        },
        "alias_events": alias_events,
        "episode": {
            "found": bool(episode),
            "injected_lessons": list(episode.get("injected_lessons", []) or []),
            "deliverable_path": episode.get("deliverable_path"),
        },
    }


def build_capabilities(trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the latest run-start capability snapshot, or an empty block."""

    snapshot = next(
        (
            event
            for event in reversed(trace_events)
            if event.get("event") == "capability_snapshot"
        ),
        {},
    )
    raw_items = snapshot.get("capabilities") if isinstance(snapshot, dict) else None
    items: list[dict[str, Any]] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict) or not item.get("cap_id"):
            continue
        raw_missing = item.get("missing_deps")
        items.append(
            {
                "cap_id": item.get("cap_id"),
                "title": item.get("title"),
                "mode": item.get("mode"),
                "state": item.get("state"),
                "missing_deps": (
                    list(raw_missing) if isinstance(raw_missing, (list, tuple)) else []
                ),
            }
        )
    counts: dict[str, int] = {}
    for item in items:
        state = str(item.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {
        "items": items,
        "by_id": {
            str(item["cap_id"]): {
                "mode": item.get("mode"),
                "state": item.get("state"),
            }
            for item in items
        },
        "counts": counts,
        "memory_generation": snapshot.get("memory_generation") if snapshot else None,
    }


# ---------------------------------------------------------------------------
# replay (A5 §3): per-step model turn + tool observations for the step report
# ---------------------------------------------------------------------------
def build_replay(view: EvidenceView) -> list[dict[str, Any]]:
    """Flatten the evidence into an ordered per-step replay for the report.

    Each entry carries the model's thinking + tool calls + token usage, the tool
    observations that followed (result text, latency, screenshot ``path``, parsed
    OBS, error), and the request context stats. This is the data behind the
    step-by-step replay — the report renders the real screenshot from
    ``image.path`` next to the model's full reasoning and each tool result.
    """

    replay: list[dict[str, Any]] = []
    for slot in view.replay_steps():
        request = slot.get("request") or {}
        response = slot.get("response") or {}
        calls = []
        for call in slot.get("tool_calls", []):
            obs = call.get("observation") or {}
            invoke = call.get("invoke") or {}
            image = obs.get("image") or {}
            calls.append(
                {
                    "tool": call.get("tool"),
                    "args": invoke.get("args"),
                    "result_text": result_text_of(obs),
                    "result_truncated": isinstance(obs.get("result_text"), dict),
                    "latency_ms": call.get("latency_ms"),
                    "error": call.get("error"),
                    "class": classify_result(call.get("result_text") or ""),
                    "obs": obs.get("obs"),
                    "image": {
                        "present": bool(image.get("present")),
                        "screen_seq": image.get("screen_seq"),
                        "bytes": image.get("bytes", 0),
                        "path": image.get("path"),
                    },
                }
            )
        replay.append(
            {
                "step": slot.get("step"),
                "thinking": response.get("thinking", ""),
                "model_tool_calls": response.get("tool_calls", []),
                "usage": response.get("usage"),
                "context": {
                    "message_count": request.get("message_count"),
                    "image_message_count": request.get("image_message_count"),
                    "pruned_screen_count": request.get("pruned_screen_count"),
                    "taskdoc_present": request.get("taskdoc_present"),
                    "context_chars": request.get("context_chars"),
                },
                "tool_calls": calls,
                "hitl": slot.get("hitl", []),
            }
        )
    return replay


# ---------------------------------------------------------------------------
# findings + recommendations
# ---------------------------------------------------------------------------
def build_findings(
    view: EvidenceView, resolver: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
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

    resolver = resolver or {}
    embedding_hits = resolver.get("embedding_launch_hits", []) or []
    if embedding_hits:
        mentions = [str(item.get("mention", "")) for item in embedding_hits]
        findings.append(
            {
                "category": "resolver_embedding_hit",
                "layer": "resolver",
                "severity": "P2",
                "title": "应用名经 embedding 路才解析并启动",
                "count": len(embedding_hits),
                "examples": mentions[:3],
                "files": add_line_numbers(["phone_agent/v2/names.py"]),
                "suggestion": (
                    "embedding 命中说明静态 registry / App-KB 尚未覆盖该叫法；"
                    "核对高频 mention，必要时补充可信别名，减少向量兜底成本。"
                ),
                "verify": "用同一 mention 再跑 launch_app，确认 exact/lexical 可稳定命中且不引入歧义。",
            }
        )
    recoveries = resolver.get("ambiguous_recoveries", []) or []
    if recoveries:
        findings.append(
            {
                "category": "resolver_ambiguous_recovered",
                "layer": "resolver",
                "severity": "Info",
                "title": "歧义候选后模型细化名称并恢复启动",
                "count": len(recoveries),
                "examples": [
                    f"{item.get('ambiguous_mention')} → {item.get('recovery_mention')} → {item.get('winner')}"
                    for item in recoveries[:3]
                ],
                "files": add_line_numbers(
                    ["phone_agent/v2/names.py", "phone_agent/v2/tools/actuation.py"]
                ),
                "suggestion": "正向记录：排序候选给出了可用恢复线索，保持 fail-closed 歧义回执。",
                "verify": "复跑歧义叫法，确认候选排序稳定且细化名称后只启动唯一包。",
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
    memory_dir: str | None = None,
) -> dict[str, Any]:
    """Assemble the full v2 ``summary.json`` dict (§3) from outcome + evidence.

    ``target`` prefers the already-redacted ``task_goal_base`` from the evidence
    ``run_start`` header (so we never re-introduce an unredacted goal), falling
    back to the caller-supplied value.
    """

    root = _artifact_root(run_dir, evidence_stream)
    source_run_id = str((view.run_start or {}).get("run_id") or run_id)
    trace_events = _read_jsonl(_trace_file(trace, root, source_run_id))
    try:
        resolver = build_resolver(trace_events)
    except Exception:  # noqa: BLE001 - optional analysis must never gate a report
        resolver = build_resolver([])
    try:
        memory = build_memory(
            trace_events,
            view,
            source_run_id=source_run_id,
            summary_run_id=run_id,
            root=root,
            memory_dir=memory_dir,
        )
    except Exception:  # noqa: BLE001 - malformed optional ledgers fail open
        memory = {
            "source_run_id": source_run_id,
            "memory_rag": {
                "mode": None,
                "status": None,
                "candidates": [],
                "candidate_count": 0,
                "actual_launch_packages": [],
                "matched_packages": [],
                "hit": False,
            },
            "alias_events": [],
            "episode": {
                "found": False,
                "injected_lessons": [],
                "deliverable_path": None,
            },
        }
    try:
        capabilities = build_capabilities(trace_events)
    except Exception:  # noqa: BLE001 - malformed optional snapshots fail open
        capabilities = build_capabilities([])
    verdict = classify_verdict(outcome, view)
    findings = build_findings(view, resolver)
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
        "resolver": resolver,
        "memory": memory,
        "capabilities": capabilities,
        "replay": build_replay(view),
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
    "build_resolver",
    "build_memory",
    "build_capabilities",
    "build_replay",
    "build_findings",
    "build_recommendations",
    "build_summary",
]
