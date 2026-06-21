"""Deterministic post-action verifier primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any, Literal

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.graph.expected_outcome import normalize_expected_outcome
from phone_agent.graph.marks import build_screen_id


VerifierStatus = Literal["success", "failure", "unknown", "blocked"]


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
    *,
    before_state: dict[str, Any],
    after_screenshot: Any,
    after_app: str,
    action_result: dict[str, Any] | None,
    before_observation: dict[str, Any] | None = None,
    after_observation: dict[str, Any] | None = None,
) -> VerifierResult:
    """Compute conservative deterministic outcome signals.

    Unknown is preferred unless there is a hard execution failure or a simple
    high-confidence signal such as launch/current_app match or screenshot hash change.
    """

    action = before_state.get("action_parsed") or {}
    result = action_result or {}
    expected = normalize_expected_outcome(
        before_state.get("expected_outcome"),
        action=action if isinstance(action, dict) else None,
        intent=before_state.get("intent_raw") if isinstance(before_state.get("intent_raw"), dict) else None,
    )
    signals = {
        "action": action.get("action") if isinstance(action, dict) else None,
        "execution_success": result.get("success") if isinstance(result, dict) else None,
        "before_app": before_state.get("current_app"),
        "after_app": after_app,
        "expected_outcome_kind": expected.kind,
    }
    evidence: dict[str, Any] = {
        "matched_postconditions": [],
        "missing_postconditions": [],
        "weak_signals": {},
        "dynamic_change_only": False,
    }
    if isinstance(result, dict) and result.get("success") is False:
        return VerifierResult(
            status="failure",
            confidence=0.9,
            signals=signals,
            hard_failure=True,
            failure_cause="app_not_responding" if "failed" in str(result.get("message", "")).lower() else "unknown",
            evidence={**evidence, "result_message_summary": result.get("message")},
        )
    if isinstance(action, dict) and action.get("action") == "Launch":
        target = str(action.get("app") or "")
        if target and target == after_app:
            return VerifierResult(
                status="success",
                confidence=0.95,
                signals={**signals, "launch_matched": True},
                evidence={**evidence, "matched_postconditions": ["app_opened"]},
            )
        if target and expected.kind == "app_opened":
            return VerifierResult(
                status="failure",
                confidence=0.85,
                signals={**signals, "launch_matched": False},
                failure_cause="wrong_page",
                evidence={**evidence, "missing_postconditions": ["app_opened"]},
            )

    before_text_blob = _observation_text(before_observation)
    text_blob = _observation_text(after_observation)
    has_after_observation_text = bool(text_blob.strip())
    focus_signals = _focus_signals(after_observation)
    if focus_signals:
        signals = {**signals, **focus_signals}
        evidence["weak_signals"] = {**evidence["weak_signals"], **focus_signals}
    matched, missing = _match_expected_text(expected.must_observe, text_blob)
    forbidden = _match_forbidden_text(expected.must_not_observe, text_blob)
    evidence["matched_postconditions"] = matched
    evidence["missing_postconditions"] = missing + forbidden
    if expected.kind in {"input_focused", "text_present", "page_opened", "target_appeared", "loading_finished"}:
        if expected.must_observe and not has_after_observation_text:
            evidence["missing_postconditions"] = ["after_observation_unavailable"]
            return VerifierResult(
                status="unknown",
                confidence=0.0,
                signals=signals,
                evidence=evidence,
            )
        if expected.kind == "input_focused":
            if missing or forbidden:
                return VerifierResult(
                    status="failure",
                    confidence=0.75,
                    signals=signals,
                    failure_cause="element_not_found",
                    evidence=evidence,
                )
            if focus_signals.get("focused_editable") or focus_signals.get("keyboard_visible"):
                return VerifierResult(
                    status="success",
                    confidence=0.9,
                    signals=signals,
                    evidence={
                        **evidence,
                        "matched_postconditions": matched or ["input_focused"],
                    },
                )
            if matched:
                evidence["missing_postconditions"] = ["focused_editable_or_keyboard_visible"]
                return VerifierResult(
                    status="unknown",
                    confidence=0.4,
                    signals=signals,
                    evidence=evidence,
                )
        if missing or forbidden:
            return VerifierResult(
                status="failure",
                confidence=0.75,
                signals=signals,
                failure_cause=_failure_cause_for_expected_kind(expected.kind),
                evidence=evidence,
            )
        if expected.kind == "loading_finished" and not has_after_observation_text:
            evidence["missing_postconditions"] = ["after_observation_unavailable"]
            return VerifierResult(
                status="unknown",
                confidence=0.0,
                signals=signals,
                evidence=evidence,
            )
        if matched or expected.kind == "loading_finished":
            return VerifierResult(
                status="success",
                confidence=0.9,
                signals=signals,
                evidence=evidence,
            )
        if expected.kind in {"page_opened", "target_appeared"} and before_text_blob and before_text_blob != text_blob:
            evidence["weak_signals"] = {**evidence["weak_signals"], "ui_tree_changed": True}
    before_hash = before_state.get("screen_hash") or before_state.get("screen_id")
    after_hash = build_screen_id(
        current_app=after_app,
        screenshot_b64=getattr(after_screenshot, "base64_data", None),
        width=int(getattr(after_screenshot, "width", 0) or 0),
        height=int(getattr(after_screenshot, "height", 0) or 0),
    )
    if before_hash and after_hash and str(before_hash) != after_hash:
        evidence["weak_signals"] = {"screen_changed": True}
        evidence["dynamic_change_only"] = expected.kind not in {"content_shifted"}
        if expected.kind == "content_shifted":
            return VerifierResult(
                status="success",
                confidence=0.75,
                signals={**signals, "screen_changed": True},
                evidence=evidence,
            )
        return VerifierResult(
            status="unknown",
            confidence=0.25,
            signals={**signals, "screen_changed": True},
            evidence=evidence,
        )
    evidence["missing_postconditions"] = missing or (
        ["postcondition_unverified"] if expected.kind != "generic" else []
    )
    return VerifierResult(status="unknown", confidence=0.0, signals=signals, evidence=evidence)


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
    if verifier.status == "failure" and verifier.confidence >= 0.7:
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": verifier.failure_cause or reflection.get("failure_cause") or "unknown",
        }
    missing = (verifier.evidence or {}).get("missing_postconditions")
    if verifier.status == "unknown" and missing and reflection.get("reflection_verdict") == "succeeded":
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": reflection.get("failure_cause") or verifier.failure_cause or "unknown",
        }
    matched = (verifier.evidence or {}).get("matched_postconditions")
    if (
        verifier.status == "unknown"
        and not matched
        and reflection.get("reflection_verdict") == "succeeded"
        and not reflection.get("reflection_has_evidence")
    ):
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "partial",
            "failure_cause": reflection.get("failure_cause") or "unknown",
        }
    return reflection


def _observation_text(observation: dict[str, Any] | None) -> str:
    if not isinstance(observation, dict):
        return ""
    safe_observation = sanitize_context_payload(observation, consumer="trace")
    chunks: list[str] = []
    _collect_visible_text(safe_observation, chunks)
    return "\n".join(chunks).lower()


def _focus_signals(observation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {}
    signals: dict[str, Any] = {}
    observed = _find_truthy_key(observation, {"focused", "is_focused", "focused_editable"})
    keyboard_visible = _find_truthy_key(observation, {"keyboard_visible", "ime_visible", "soft_keyboard_visible"})
    editable_present = _contains_editable_node(observation)
    if observed:
        signals["focused_editable"] = True
    elif editable_present:
        signals["editable_present"] = True
    if keyboard_visible is not None:
        signals["keyboard_visible"] = keyboard_visible
    top_activity = _find_string_key(observation, {"top_activity", "focused_window", "current_window"})
    if top_activity:
        signals["top_activity"] = top_activity
    return signals


def _find_truthy_key(value: Any, keys: set[str]) -> bool | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in keys and isinstance(item, bool):
                return item
            found = _find_truthy_key(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_truthy_key(item, keys)
            if found is not None:
                return found
    return None


def _find_string_key(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in keys and isinstance(item, str) and item.strip():
                return item.strip()[:160]
            found = _find_string_key(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_string_key(item, keys)
            if found:
                return found
    return None


def _contains_editable_node(value: Any) -> bool:
    if isinstance(value, dict):
        role = str(value.get("role") or value.get("class") or value.get("class_name") or "").lower()
        if "edittext" in role or "textfield" in role or "input" in role:
            return True
        if value.get("editable") is True:
            return True
        return any(_contains_editable_node(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_editable_node(item) for item in value)
    return False


VISIBLE_TEXT_KEYS = {
    "text",
    "text_summary",
    "label",
    "content_desc",
    "content-description",
    "visible_text",
    "observed_text",
    "hint",
    "value",
}


def _collect_visible_text(value: Any, chunks: list[str], key: str | None = None) -> None:
    normalized = (key or "").lower()
    if isinstance(value, str):
        if normalized in VISIBLE_TEXT_KEYS:
            chunks.append(value)
    elif isinstance(value, dict):
        for child_key, item in value.items():
            _collect_visible_text(item, chunks, str(child_key))
    elif isinstance(value, list):
        for item in value:
            _collect_visible_text(item, chunks, key)


def _match_expected_text(expected: list[str], text_blob: str) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for item in expected:
        if item == "private_text_unverifiable":
            missing.append("private_text_unverifiable")
            continue
        if item.startswith("sha256:"):
            digest = item.split(":", 1)[1]
            if _text_blob_contains_hash(text_blob, digest):
                matched.append(item)
            else:
                missing.append(item)
            continue
        normalized = item.lower()
        if normalized and normalized in text_blob:
            matched.append(item)
        else:
            missing.append(item)
    return matched, missing


def _match_forbidden_text(forbidden: list[str], text_blob: str) -> list[str]:
    present: list[str] = []
    for item in forbidden:
        if item == "private_text_unverifiable":
            continue
        if item.startswith("sha256:"):
            digest = item.split(":", 1)[1]
            if _text_blob_contains_hash(text_blob, digest):
                present.append(f"forbidden:{item}")
            continue
        normalized = item.lower()
        if normalized and normalized in text_blob:
            present.append(f"forbidden:{item}")
    return present


def _text_blob_contains_hash(text_blob: str, digest: str) -> bool:
    for line in text_blob.splitlines():
        text = line.strip()
        if text and hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] == digest:
            return True
    return False


def _failure_cause_for_expected_kind(kind: str) -> str:
    if kind in {"input_focused", "text_present"}:
        return "element_not_found"
    if kind in {"page_opened", "target_appeared"}:
        return "wrong_page"
    if kind == "loading_finished":
        return "network_or_loading"
    return "unknown"
