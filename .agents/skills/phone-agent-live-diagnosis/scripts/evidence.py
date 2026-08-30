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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

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
    "result_text_of",
    "EvidenceView",
]
