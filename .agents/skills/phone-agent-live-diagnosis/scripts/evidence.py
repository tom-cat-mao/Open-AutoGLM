"""Read + index the diagnostic evidence JSONL stream.

Per ``outputs/design-council/ROUND2-D1.md`` §5. The
:class:`~phone_agent.v2.middleware.diagnostic.DiagnosticEvidenceMiddleware`
writes one JSON object per line to ``<run_id>.evidence.jsonl``; this module reads
that stream back and exposes a small typed view (``EvidenceView``) the analyzer
builds ``summary.json`` from. ``parse_obs_block`` mirrors the middleware's
OBS parsing so the analyzer can re-derive ``current_app`` / ``screen_seq`` /
``mark_count`` from any ``[OBS]`` text it encounters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# WP-G2a windowed marks contract. The ``marks (K):`` line tail carries the
# ``windowed/v1 source=...`` badge only in the grouped format; each window head
# is ``W<n> <TYPE> <package> layer=<n> <flags...>`` and each mark line is
# ``<mark_id> | <role> | <text> | <center> [| op=<x>] [| path=<y>]``.
_WINDOW_MARKER = "windowed/"
_OP_LEVELS = ("confirmed", "likely", "blocked", "unknown")
_WINDOW_HEAD_RE = re.compile(r"^(W\d+)\b(.*)$")

# Event discriminants (mirror the middleware schema, §1).
EVENTS = (
    "run_start",
    "model_request",
    "model_response",
    "taskdoc_snapshot",
    "tool_invoke",
    "tool_observation",
    "hitl_decision",
    "stagnation_nudge",
    "run_end",
)


def read_evidence(path: str | Path) -> list[dict[str, Any]]:
    """Read a ``.evidence.jsonl`` file into a list of event dicts.

    Blank lines are skipped; malformed lines are tolerated (skipped) so a
    partially-flushed stream from an interrupted run still analyzes.
    """

    p = Path(path)
    events: list[dict[str, Any]] = []
    if not p.exists():
        return events
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def parse_obs_block(text: str) -> dict[str, Any] | None:
    """Parse ``[OBS] app=<app> screen#<n>\\nmarks (<c>): ...`` -> dict or None.

    Returns ``{current_app, screen_seq, mark_count}``. Mirrors the middleware's
    inline parser so re-derivation at analysis time matches what was recorded.
    """

    if not text or "[OBS]" not in text:
        return None
    idx = text.find("[OBS]")
    segment = text[idx:]
    current_app: str | None = None
    screen_seq: int | None = None
    mark_count: int | None = None

    a = segment.find("app=")
    if a != -1:
        rest = segment[a + len("app=") :]
        parts = rest.split()
        current_app = parts[0] if parts else None

    s = segment.find("screen#")
    if s != -1:
        digits = ""
        for ch in segment[s + len("screen#") :]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            screen_seq = int(digits)

    m = segment.find("marks (")
    if m != -1:
        digits = ""
        for ch in segment[m + len("marks (") :]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            mark_count = int(digits)

    return {
        "current_app": current_app,
        "screen_seq": screen_seq,
        "mark_count": mark_count,
    }


def parse_obs_windows(text: str) -> dict[str, Any] | None:
    """Parse the WP-G2a windowed marks section of an ``[OBS]`` block.

    The grouped format tags the ``marks (K):`` line tail with
    ``windowed/<schema> source=<src>`` and then emits, per window, a head line
    ``W<n> <TYPE_*> <package> layer=<n> <flags...>`` followed by indented mark
    lines carrying optional ``op=<confirmed|likely|blocked|unknown>`` and
    ``path=<container path>`` fields.

    Returns a dict::

        {
            "present": True,
            "schema": "v1",
            "source": "shell_windows",
            "windows": [
                {"id": "W1", "type": "TYPE_SYSTEM", "package": "...",
                 "layer": 42, "flags": ["active", "focus"],
                 "covered_by": None, "mark_count": 1,
                 "op_counts": {...}, "marks": [{mark_id, op, path}, ...]},
                ...
            ],
            "window_count": N,
            "op_counts": {"confirmed": .., "likely": .., "blocked": ..,
                          "unknown": .., "unspecified": ..},
            "blocked_mark_ids": ["ax_3@e12", ...],
        }

    Returns ``None`` for the legacy flat format (no ``windowed/`` badge) or when
    the text carries no ``[OBS]`` header. Never raises: malformed lines are
    tolerated so an old/partly-written observation still analyzes (fail-open).
    """

    if not text or "[OBS]" not in text:
        return None
    idx = text.find("[OBS]")
    segment = text[idx:]

    m = segment.find("marks (")
    if m == -1:
        return None
    header_end = segment.find("\n", m)
    if header_end == -1:
        # No body after the marks header -> nothing windowed to parse.
        return None
    header_line = segment[m:header_end]
    if _WINDOW_MARKER not in header_line:
        return None  # legacy flat format: not our concern (fail-open).

    schema: str | None = None
    source: str | None = None
    win_match = re.search(r"windowed/(\S+)", header_line)
    if win_match:
        schema = win_match.group(1)
    src_match = re.search(r"source=(\S+)", header_line)
    if src_match:
        source = src_match.group(1)

    windows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    total_op_counts = {level: 0 for level in _OP_LEVELS}
    total_op_counts["unspecified"] = 0
    blocked_mark_ids: list[str] = []

    for raw in segment[header_end + 1 :].splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        head = _WINDOW_HEAD_RE.match(stripped)
        # A window head line starts with ``W<digits>`` and is NOT a mark line
        # (mark lines contain the ``|`` field separator).
        if head and "|" not in stripped:
            current = _parse_window_head(head.group(1), head.group(2))
            windows.append(current)
            continue
        if "|" not in stripped:
            continue  # not a mark line and not a window head: skip.
        mark = _parse_windowed_mark_line(stripped)
        if mark is None:
            continue
        if current is None:
            # A mark before any window head (defensive): synthesize a bucket.
            current = _parse_window_head("W?", "")
            windows.append(current)
        current["marks"].append(mark)
        current["mark_count"] += 1
        op = mark.get("op")
        bucket = op if op in _OP_LEVELS else "unspecified"
        current["op_counts"][bucket] = current["op_counts"].get(bucket, 0) + 1
        total_op_counts[bucket] = total_op_counts.get(bucket, 0) + 1
        if op == "blocked" and mark.get("mark_id"):
            blocked_mark_ids.append(mark["mark_id"])

    return {
        "present": True,
        "schema": schema,
        "source": source,
        "windows": windows,
        "window_count": len(windows),
        "op_counts": total_op_counts,
        "blocked_mark_ids": blocked_mark_ids,
    }


def _parse_window_head(win_id: str, rest: str) -> dict[str, Any]:
    """Parse a ``W<n> <TYPE_*> <package> layer=<n> <flags...>`` head line.

    ``rest`` is everything after the ``W<n>`` token. Type is the first
    ``TYPE_*`` token, package the next bare token, ``layer=`` / ``covered_by=``
    are key/value fields, and any remaining bare tokens are flags (active,
    focus, ...). Missing fields degrade to ``None`` rather than raising.
    """

    tokens = rest.split()
    win_type: str | None = None
    package: str | None = None
    layer: int | None = None
    covered_by: str | None = None
    flags: list[str] = []
    for token in tokens:
        if token.startswith("layer="):
            value = token[len("layer=") :]
            try:
                layer = int(value)
            except ValueError:
                layer = None
        elif token.startswith("covered_by="):
            covered_by = token[len("covered_by=") :] or None
        elif "=" in token:
            # Unknown key=value field: keep as a flag token for visibility.
            flags.append(token)
        elif win_type is None and token.startswith("TYPE_"):
            win_type = token
        elif package is None and "." in token:
            package = token
        else:
            flags.append(token)
    return {
        "id": win_id,
        "type": win_type,
        "package": package,
        "layer": layer,
        "covered_by": covered_by,
        "flags": flags,
        "mark_count": 0,
        "op_counts": {},
        "marks": [],
    }


def _parse_windowed_mark_line(line: str) -> dict[str, Any] | None:
    """Parse one indented mark line into ``{mark_id, role, op, path}``.

    Fields are ``|``-separated; the first field is the mark id, the second the
    role. Optional ``op=`` / ``path=`` fields may appear in any field position.
    Returns ``None`` only if there is no usable mark id.
    """

    parts = [seg.strip() for seg in line.split("|")]
    if not parts or not parts[0]:
        return None
    mark_id = parts[0]
    role = parts[1] if len(parts) > 1 else None
    op: str | None = None
    path: str | None = None
    for seg in parts[1:]:
        if seg.startswith("op="):
            op = seg[len("op=") :] or None
        elif seg.startswith("path="):
            path = seg[len("path=") :] or None
    return {"mark_id": mark_id, "role": role, "op": op, "path": path}


def result_text_of(observation: dict[str, Any]) -> str:
    """Extract the flat ``result_text`` from a ``tool_observation`` event.

    ``result_text`` is either a redacted string, or an over-``DIAG_MAX_TEXT``
    truncation marker ``{_truncated, _orig_len, text}``; both yield the head text.
    """

    value = observation.get("result_text")
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return str(value or "")


@dataclass
class EvidenceView:
    """Indexed view over a diagnostic evidence stream.

    Splits the flat event list into typed buckets and pairs each
    ``tool_invoke`` with its following ``tool_observation`` (``tool_calls``),
    which is the unit the analyzer's tool-health / grounding / finish-gate
    builders iterate over.
    """

    events: list[dict[str, Any]]
    run_start: dict[str, Any] | None = None
    run_end: dict[str, Any] | None = None
    model_requests: list[dict[str, Any]] = field(default_factory=list)
    model_responses: list[dict[str, Any]] = field(default_factory=list)
    taskdoc_snapshots: list[dict[str, Any]] = field(default_factory=list)
    invocations: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    hitl_decisions: list[dict[str, Any]] = field(default_factory=list)
    stagnation_nudges: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_events(cls, events: Iterable[dict[str, Any]]) -> "EvidenceView":
        events = list(events)
        view = cls(events=events)
        pending_invoke: dict[str, Any] | None = None
        for ev in events:
            kind = ev.get("event")
            if kind == "run_start":
                view.run_start = ev
            elif kind == "run_end":
                view.run_end = ev
            elif kind == "model_request":
                view.model_requests.append(ev)
            elif kind == "model_response":
                view.model_responses.append(ev)
            elif kind == "taskdoc_snapshot":
                view.taskdoc_snapshots.append(ev)
            elif kind == "tool_invoke":
                view.invocations.append(ev)
                pending_invoke = ev
            elif kind == "tool_observation":
                view.observations.append(ev)
                view.tool_calls.append(
                    {
                        "step": ev.get("step"),
                        "tool": ev.get("tool"),
                        "invoke": pending_invoke,
                        "observation": ev,
                        "result_text": result_text_of(ev),
                        "error": ev.get("error"),
                        "latency_ms": ev.get("latency_ms"),
                    }
                )
                pending_invoke = None
            elif kind == "hitl_decision":
                view.hitl_decisions.append(ev)
            elif kind == "stagnation_nudge":
                view.stagnation_nudges.append(ev)
        return view

    def latest_taskdoc(self) -> dict[str, Any] | None:
        """The most recent taskdoc snapshot (the terminal task board)."""

        return self.taskdoc_snapshots[-1] if self.taskdoc_snapshots else None

    def finish_calls(self) -> list[dict[str, Any]]:
        """All ``finish`` tool calls (invoke+observation), in order."""

        return [c for c in self.tool_calls if c.get("tool") == "finish"]

    def replay_steps(self) -> list[dict[str, Any]]:
        """Assemble per-step replay records for the step-by-step report (A5 §3).

        One record per model step, in order, each carrying the model turn
        (thinking + tool calls + token usage), the tool observations that
        followed it (result text, latency, screenshot ``path``/summary, parsed
        OBS), and the model_request context stats. The step index is the
        ``step`` field the middleware stamps on every event.
        """

        by_step: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []

        def _slot(step: Any) -> dict[str, Any]:
            if step not in by_step:
                by_step[step] = {
                    "step": step,
                    "request": None,
                    "response": None,
                    "tool_calls": [],
                    "hitl": [],
                }
                order.append(step)
            return by_step[step]

        for req in self.model_requests:
            _slot(req.get("step"))["request"] = req
        for resp in self.model_responses:
            _slot(resp.get("step"))["response"] = resp
        for call in self.tool_calls:
            _slot(call.get("step"))["tool_calls"].append(call)
        for decision in self.hitl_decisions:
            step = decision.get("step")
            if step is not None:
                _slot(step)["hitl"].append(decision)

        def _sort_key(step: Any) -> tuple[int, Any]:
            return (0, step) if isinstance(step, (int, float)) else (1, str(step))

        return [by_step[s] for s in sorted(order, key=_sort_key)]


__all__ = [
    "EVENTS",
    "read_evidence",
    "parse_obs_block",
    "parse_obs_windows",
    "result_text_of",
    "EvidenceView",
]
