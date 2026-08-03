"""Pure safety gate decisions for validated actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SafetyRoute = Literal["approved", "confirm", "takeover", "rejected"]


@dataclass(frozen=True)
class SafetyDecision:
    """Decision emitted by the safety gate. It never executes tools."""

    route: SafetyRoute
    interrupt_type: str | None = None
    reason: str | None = None
    sanitized_trace_payload: dict[str, Any] | None = None


def decide_safety(action: dict[str, Any]) -> SafetyDecision:
    """Return a route/interrupt decision for a validated action."""

    metadata = action.get("_metadata")
    if metadata == "finish":
        return SafetyDecision(route="approved", reason="finish")
    if metadata != "do":
        return SafetyDecision(route="rejected", reason="invalid_metadata")

    action_name = action.get("action")
    if action_name == "Take_over":
        return SafetyDecision(
            route="takeover",
            interrupt_type="takeover",
            reason="manual_handoff_required",
            sanitized_trace_payload={"action": action_name, "interrupt_type": "takeover"},
        )
    if action_name == "Tap" and "message" in action:
        return SafetyDecision(
            route="confirm",
            interrupt_type="confirmation",
            reason="sensitive_tap_requires_confirmation",
            sanitized_trace_payload={"action": action_name, "interrupt_type": "confirmation"},
        )
    if action_name == "Locate":
        # Internal visual search: pure observation helper, no device side effect,
        # no HITL, no Goal progress. Never routes to confirm/takeover.
        return SafetyDecision(
            route="approved",
            reason="internal_locate_no_side_effect",
            sanitized_trace_payload={"action": action_name, "interrupt_type": None},
        )
    return SafetyDecision(
        route="approved",
        reason="safe_to_execute",
        sanitized_trace_payload={"action": action_name},
    )
