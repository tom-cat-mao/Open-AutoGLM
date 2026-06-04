"""Deterministic post-action verifier primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from phone_agent.graph.marks import build_screen_id


VerifierStatus = Literal["success", "failure", "unknown"]


@dataclass(frozen=True)
class VerifierResult:
    status: VerifierStatus = "unknown"
    confidence: float = 0.0
    signals: dict[str, Any] = field(default_factory=dict)
    hard_failure: bool = False
    failure_cause: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_action_outcome(
    *, before_state: dict[str, Any], after_screenshot: Any, after_app: str, action_result: dict[str, Any] | None
) -> VerifierResult:
    """Compute conservative deterministic outcome signals.

    Unknown is preferred unless there is a hard execution failure or a simple
    high-confidence signal such as launch/current_app match or screenshot hash change.
    """

    action = before_state.get("action_parsed") or {}
    result = action_result or {}
    signals = {
        "action": action.get("action") if isinstance(action, dict) else None,
        "execution_success": result.get("success") if isinstance(result, dict) else None,
        "before_app": before_state.get("current_app"),
        "after_app": after_app,
    }
    if isinstance(result, dict) and result.get("success") is False:
        return VerifierResult(
            status="failure",
            confidence=0.9,
            signals=signals,
            hard_failure=True,
            failure_cause="app_not_responding" if "failed" in str(result.get("message", "")).lower() else "unknown",
            evidence={"result_message_summary": result.get("message")},
        )
    if isinstance(action, dict) and action.get("action") == "Launch":
        target = str(action.get("app") or "")
        if target and target == after_app:
            return VerifierResult(status="success", confidence=0.95, signals={**signals, "launch_matched": True})
    before_hash = before_state.get("screen_hash") or before_state.get("screen_id")
    after_hash = build_screen_id(
        current_app=after_app,
        screenshot_b64=getattr(after_screenshot, "base64_data", None),
        width=int(getattr(after_screenshot, "width", 0) or 0),
        height=int(getattr(after_screenshot, "height", 0) or 0),
    )
    if before_hash and after_hash and str(before_hash) != after_hash:
        return VerifierResult(status="success", confidence=0.55, signals={**signals, "screen_changed": True})
    return VerifierResult(status="unknown", confidence=0.0, signals=signals)


def merge_verifier_with_reflection(verifier: VerifierResult, reflection: dict[str, Any]) -> dict[str, Any]:
    """Apply precedence: hard failure > model reflection; unknown falls back."""

    if verifier.hard_failure:
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": verifier.failure_cause or reflection.get("failure_cause") or "unknown",
        }
    if verifier.status == "success" and verifier.confidence >= 0.9:
        return {**reflection, "action_succeeded": True, "reflection_verdict": "succeeded", "failure_cause": None}
    return reflection
