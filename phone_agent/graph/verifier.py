"""Deterministic post-action verifier primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
from typing import Any, Literal

from phone_agent.config.apps import APP_PACKAGES, get_package_name, normalize_app_name
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY
from phone_agent.graph.compatibility_adapters import PageSignalAdapter
from phone_agent.graph.expected_outcome import normalize_expected_outcome
from phone_agent.graph.marks import build_screen_id

SELECTED_OBJECT_TEXT_MATCH_CONFIDENCE = DEFAULT_VERIFICATION_POLICY.value(
    "selected_object_text_match_confidence"
)

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
    page_signal_adapter: PageSignalAdapter | None = None,
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
        intent=(
            before_state.get("intent_raw")
            if isinstance(before_state.get("intent_raw"), dict)
            else None
        ),
    )
    signals = {
        "action": action.get("action") if isinstance(action, dict) else None,
        "execution_success": (
            result.get("success") if isinstance(result, dict) else None
        ),
        "before_app": before_state.get("current_app"),
        "after_app": after_app,
        "expected_outcome_kind": expected.kind,
    }
    evidence: dict[str, Any] = {
        "matched_postconditions": [],
        "missing_postconditions": [],
        "progress_signals": {},
        "weak_signals": {},
        "dynamic_change_only": False,
    }
    if isinstance(result, dict) and result.get("success") is False:
        return VerifierResult(
            status="failure",
            confidence=0.9,
            signals=signals,
            hard_failure=True,
            failure_cause=(
                "app_not_responding"
                if "failed" in str(result.get("message", "")).lower()
                else "unknown"
            ),
            evidence={**evidence, "result_message_summary": result.get("message")},
        )
    if isinstance(action, dict) and action.get("action") == "Launch":
        target = str(action.get("app") or "")
        target_package = _package_for_app_name(target)
        after_package = _package_for_app_name(str(after_app or ""))
        after_component = " ".join(
            str(value or "")
            for value in (
                after_app,
                _find_string_key(
                    after_observation,
                    {"top_activity", "focused_window", "current_window"},
                ),
            )
        )
        if target_package and (
            target_package == after_package or target_package in after_component
        ):
            return VerifierResult(
                status="success",
                confidence=0.95,
                signals={
                    **signals,
                    "launch_matched": True,
                    "launch_match_type": "package",
                },
                evidence={**evidence, "matched_postconditions": ["app_opened"]},
            )
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

    if expected.kind == "surface_changed":
        before_surface = _surface_identity(before_observation)
        after_surface = _surface_identity(after_observation)
        before_screen = _find_string_key(before_observation, {"screen_id"})
        after_screen = _find_string_key(after_observation, {"screen_id"})
        changed = bool(
            (before_surface and after_surface and before_surface != after_surface)
            or (before_screen and after_screen and before_screen != after_screen)
        )
        if changed:
            return VerifierResult(
                status="success",
                confidence=0.9,
                signals={**signals, "surface_changed": True},
                evidence={**evidence, "matched_postconditions": ["surface_changed"]},
            )
        if (before_surface and after_surface) or (before_screen and after_screen):
            return VerifierResult(
                status="failure",
                confidence=0.75,
                signals={**signals, "surface_changed": False},
                failure_cause="wrong_page",
                evidence={**evidence, "missing_postconditions": ["surface_changed"]},
            )
        return VerifierResult(
            status="unknown",
            confidence=0.0,
            signals=signals,
            evidence={**evidence, "missing_postconditions": ["surface_unavailable"]},
        )

    before_text_blob = _observation_text(before_observation)
    text_blob = _observation_text(after_observation)
    has_after_observation_text = bool(text_blob.strip())
    selected_object_signals = _selected_object_signals(
        expected.to_dict(),
        after_observation,
        text_blob,
        page_signal_adapter,
        before_observation=before_observation,
    )
    if selected_object_signals:
        signals = {**signals, **selected_object_signals}
        evidence["selected_object_signals"] = selected_object_signals
        # Disconfirming signals are evaluated before confirming ones: verification is
        # fail-closed, so evidence that the target was not reached outranks evidence
        # that it might have been.
        if selected_object_signals.get("same_surface_still_visible"):
            evidence["missing_postconditions"] = ["selected_object_detail_not_opened"]
            return VerifierResult(
                status="failure",
                confidence=0.75,
                signals=signals,
                failure_cause="wrong_page",
                evidence=evidence,
            )
        if selected_object_signals.get("wrong_detail_opened"):
            evidence["missing_postconditions"] = ["selected_object_mismatch"]
            return VerifierResult(
                status="failure",
                confidence=0.8,
                signals=signals,
                failure_cause="wrong_page",
                evidence=evidence,
            )
        if selected_object_signals.get("selected_object_match"):
            evidence["matched_postconditions"] = ["selected_object_match"]
            # Content appearing on the new screen shows the action landed somewhere
            # plausible, but not that the trajectory advanced or the Goal moved.
            return VerifierResult(
                status="success",
                confidence=SELECTED_OBJECT_TEXT_MATCH_CONFIDENCE,
                signals=signals,
                evidence=evidence,
            )
    focus_signals = _focus_signals(after_observation)
    if focus_signals:
        signals = {**signals, **focus_signals}
        evidence["weak_signals"] = {**evidence["weak_signals"], **focus_signals}
    progress_signals = _progress_signals(
        action=action if isinstance(action, dict) else {},
        expected_kind=expected.kind,
        observation=after_observation,
        text_blob=text_blob,
        focus_signals=focus_signals,
    )
    if progress_signals:
        signals = {**signals, **progress_signals}
        evidence["progress_signals"] = progress_signals
    matched, missing = _match_expected_text(expected.must_observe, text_blob)
    forbidden = _match_forbidden_text(expected.must_not_observe, text_blob)
    evidence["matched_postconditions"] = matched
    evidence["missing_postconditions"] = missing + forbidden
    if expected.kind in {
        "input_focused",
        "text_present",
        "page_opened",
        "target_appeared",
        "loading_finished",
    }:
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
                if progress_signals:
                    evidence["missing_postconditions"] = missing + forbidden
                    return VerifierResult(
                        status="unknown",
                        confidence=0.45,
                        signals=signals,
                        failure_cause="element_not_found",
                        evidence=evidence,
                    )
                return VerifierResult(
                    status="failure",
                    confidence=0.75,
                    signals=signals,
                    failure_cause="element_not_found",
                    evidence=evidence,
                )
            if focus_signals.get("focused_editable") or focus_signals.get(
                "keyboard_visible"
            ):
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
                evidence["missing_postconditions"] = [
                    "focused_editable_or_keyboard_visible"
                ]
                return VerifierResult(
                    status="unknown",
                    confidence=0.4,
                    signals=signals,
                    evidence=evidence,
                )
        if missing or forbidden:
            if expected.kind in {"page_opened", "target_appeared"} and progress_signals:
                return VerifierResult(
                    status="unknown",
                    confidence=0.45,
                    signals=signals,
                    failure_cause=_failure_cause_for_expected_kind(expected.kind),
                    evidence=evidence,
                )
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
        if expected.kind == "text_present" and progress_signals.get(
            "typed_text_present"
        ):
            return VerifierResult(
                status="success",
                confidence=0.9,
                signals=signals,
                evidence={
                    **evidence,
                    "matched_postconditions": ["typed_text_present"],
                },
            )
        if (
            expected.kind in {"page_opened", "target_appeared"}
            and before_text_blob
            and before_text_blob != text_blob
        ):
            evidence["weak_signals"] = {
                **evidence["weak_signals"],
                "ui_tree_changed": True,
            }
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
            evidence["missing_postconditions"] = ["content_shift_unverified"]
            return VerifierResult(
                status="unknown",
                confidence=0.25,
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
    return VerifierResult(
        status="unknown", confidence=0.0, signals=signals, evidence=evidence
    )


def merge_verifier_with_reflection(
    verifier: VerifierResult, reflection: dict[str, Any]
) -> dict[str, Any]:
    """Apply local postcondition precedence after reflection has run."""

    if verifier.hard_failure:
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": verifier.failure_cause
            or reflection.get("failure_cause")
            or "unknown",
        }
    if verifier.status == "success" and verifier.confidence >= 0.9:
        return {
            **reflection,
            "action_succeeded": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
        }
    selected = (verifier.evidence or {}).get("selected_object_signals") or {}
    if (
        verifier.status == "success"
        and selected.get("selected_object_text_match") is True
        and selected.get("selected_object_surface_changed") is True
    ):
        return {
            **reflection,
            "action_succeeded": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
        }
    if verifier.status == "failure" and verifier.confidence >= 0.7:
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": verifier.failure_cause
            or reflection.get("failure_cause")
            or "unknown",
        }
    missing = (verifier.evidence or {}).get("missing_postconditions")
    if (
        verifier.status == "unknown"
        and missing
        and reflection.get("reflection_verdict") == "succeeded"
    ):
        return {
            **reflection,
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": reflection.get("failure_cause")
            or verifier.failure_cause
            or "unknown",
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
    chunks: list[str] = []
    _collect_visible_text(observation, chunks)
    return "\n".join(chunks).lower()


def _focus_signals(observation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {}
    signals: dict[str, Any] = {}
    observed = _find_truthy_key(
        observation, {"focused", "is_focused", "focused_editable"}
    )
    keyboard_visible = _find_truthy_key(
        observation, {"keyboard_visible", "ime_visible", "soft_keyboard_visible"}
    )
    editable_present = _contains_editable_node(observation)
    if observed:
        signals["focused_editable"] = True
    elif editable_present:
        signals["editable_present"] = True
    if keyboard_visible is not None:
        signals["keyboard_visible"] = keyboard_visible
    top_activity = _find_string_key(
        observation, {"top_activity", "focused_window", "current_window"}
    )
    if top_activity:
        signals["top_activity"] = top_activity
    return signals


def _progress_signals(
    *,
    action: dict[str, Any],
    expected_kind: str,
    observation: dict[str, Any] | None,
    text_blob: str,
    focus_signals: dict[str, Any],
) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    if focus_signals.get("editable_present"):
        signals["editable_present"] = True
    if focus_signals.get("focused_editable"):
        signals["focused_editable"] = True
    if focus_signals.get("keyboard_visible"):
        signals["keyboard_visible"] = True
    top_activity = str(focus_signals.get("top_activity") or "").lower()
    if "search" in top_activity or "搜索" in top_activity:
        signals["search_activity"] = True
    if _contains_search_button(observation):
        signals["search_button_present"] = True
    action_text = action.get("text") if isinstance(action, dict) else None
    if isinstance(action_text, str) and action_text:
        normalized_text = action_text.lower()
        if normalized_text in text_blob:
            signals["typed_text_present"] = True
    if expected_kind == "input_focused" and "input" in text_blob:
        signals["input_hint_present"] = True
    return signals


def _package_for_app_name(app_name: str) -> str | None:
    canonical = normalize_app_name(app_name)
    if canonical:
        return get_package_name(canonical)
    return APP_PACKAGES.get(app_name)


def _contains_search_button(value: Any) -> bool:
    if isinstance(value, dict):
        role = str(
            value.get("role") or value.get("class") or value.get("class_name") or ""
        ).lower()
        text = " ".join(
            str(value.get(key) or "")
            for key in (
                "text",
                "text_summary",
                "label",
                "content_desc",
                "content-description",
                "visible_text",
                "value",
            )
        ).lower()
        if ("button" in role or "textview" in role) and (
            "search" in text or "搜索" in text
        ):
            return True
        return any(_contains_search_button(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_search_button(item) for item in value)
    return False


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
        role = str(
            value.get("role") or value.get("class") or value.get("class_name") or ""
        ).lower()
        if "edittext" in role or "textfield" in role or "input" in role:
            return True
        if value.get("editable") is True:
            return True
        return any(_contains_editable_node(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_editable_node(item) for item in value)
    return False


# Android widget class names as they reach us from the accessibility tree: a single
# CamelCase identifier, optionally with an inner-class suffix ("ActionBar$Tab").
# Real on-screen labels do not take this shape.
CLASS_NAME_EVIDENCE_RE = re.compile(r"[A-Z][A-Za-z0-9]*(?:\$[A-Za-z0-9]+)*")

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


def _collect_visible_text(
    value: Any, chunks: list[str], key: str | None = None
) -> None:
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


def _match_expected_text(
    expected: list[str], text_blob: str
) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for item in expected:
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
    for text in _hashable_text_segments(text_blob):
        if hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] == digest:
            return True
    return False


def _hashable_text_segments(text_blob: str) -> list[str]:
    segments: set[str] = set()
    for line in text_blob.splitlines():
        text = line.strip()
        if not text:
            continue
        segments.add(text)
        compact = "".join(text.split())
        if compact:
            segments.add(compact)
        for token in re.split(r"[\s|:：,，/\\[\\]()（）【】{}<>《》\"'、]+", text):
            token = token.strip()
            if token:
                segments.add(token)
        for ngram_size in (2, 3, 4):
            if len(compact) >= ngram_size:
                for index in range(0, len(compact) - ngram_size + 1):
                    segments.add(compact[index : index + ngram_size])
    return list(segments)


def _failure_cause_for_expected_kind(kind: str) -> str:
    if kind in {"input_focused", "text_present"}:
        return "element_not_found"
    if kind in {"page_opened", "target_appeared"}:
        return "wrong_page"
    if kind == "loading_finished":
        return "network_or_loading"
    return "unknown"


def _normalize_surface_identity(value: Any) -> str:
    """Reduce a surface string to a comparable bare activity component.

    The before and after payloads are produced by different code paths that do
    not agree on shape: ``state_before_observation_payload`` only exposes
    ``snapshot.foreground_activity`` (a bare activity class), while the after
    payload additionally carries ``device_signals.top_activity`` as an Android
    ``package/activity`` component. Comparing the raw strings made the two sides
    structurally unequal on every single step, so one physical screen reported
    ``selected_object_surface_changed=True`` and a ``surface_changed``
    postcondition matched even when nothing had navigated. Normalizing both
    sides to the activity component restores a comparable identity.

    Two packages exposing the same fully-qualified activity class would collide
    here, but activity names are fully qualified and app identity is tracked
    separately via ``before_app`` / ``after_app``.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    package, separator, activity = text.partition("/")
    if not separator:
        return text
    package = package.strip()
    activity = activity.strip()
    if not activity:
        return package
    # Android shorthand: "com.pkg/.Inner" denotes activity "com.pkg.Inner".
    if activity.startswith("."):
        return f"{package}{activity}"
    return activity


def _surface_identity(observation: dict[str, Any] | None) -> str:
    """Return the foreground activity/window identifying the current surface.

    Unlike ``screen_id``, this is not derived from screen content, so it stays
    stable when a feed reorders and still changes when navigation happens.
    """

    if not isinstance(observation, dict):
        return ""
    signals = observation.get("device_signals")
    if isinstance(signals, dict):
        for key in ("top_activity", "focused_window"):
            value = signals.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_surface_identity(value)
    value = _find_string_key(
        observation,
        {"top_activity", "foreground_activity", "focused_window", "current_window"},
    )
    return _normalize_surface_identity(value)


def is_content_bearing_evidence(value: Any) -> bool:
    """Return whether *value* is on-screen content rather than a widget type name.

    Accessibility nodes without text used to report their Java class name as
    ``text_summary``. Because anonymous containers appear on every screen, using
    such a value as evidence made containment against the screen text blob true no
    matter what was tapped, so it is not admissible evidence.
    """

    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return not CLASS_NAME_EVIDENCE_RE.fullmatch(text)


def _selected_object_signals(
    expected: dict[str, Any],
    observation: dict[str, Any] | None,
    text_blob: str,
    page_signal_adapter: PageSignalAdapter | None,
    *,
    before_observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(expected, dict):
        return {}
    object_type = expected.get("object_type")
    evidence_summary = expected.get("evidence_summary")
    expected_page_type = str(expected.get("expected_page_type") or "")
    expected_rank = expected.get("expected_rank")
    if not any(
        isinstance(value, str) and value
        for value in (object_type, evidence_summary, expected_page_type)
    ):
        return {}
    text_match = bool(
        is_content_bearing_evidence(evidence_summary)
        and str(evidence_summary).casefold() in text_blob.casefold()
    )
    legacy_shadow_detail = False
    legacy_shadow_feed = False
    if page_signal_adapter is not None:
        legacy_shadow_detail = page_signal_adapter.detail_signal(
            observation, text_blob, expected_page_type
        )
        legacy_shadow_feed = page_signal_adapter.feed_signal(observation, text_blob)
    if expected_page_type == "input_focused":
        detail_signal = bool(
            _focus_signals(observation).get("focused_editable")
            or _focus_signals(observation).get("keyboard_visible")
        )
    else:
        detail_signal = False
    signals: dict[str, Any] = {
        "selected_object_expected_page_type": expected_page_type or None,
        "selected_object_expected_rank": (
            expected_rank if isinstance(expected_rank, int) else None
        ),
        "selected_object_text_match": text_match,
        "selected_object_detail_signal": detail_signal,
        "legacy_shadow_detail_signal": legacy_shadow_detail,
        "legacy_shadow_feed_signal": legacy_shadow_feed,
    }
    # Surface comparison. These two keys had readers in the verifier ladder and in
    # GoalEvaluator but no producer: the only path that could set them ran through
    # `page_signal_adapter`, which both runtime call sites pass as None.
    before_surface = _surface_identity(before_observation)
    after_surface = _surface_identity(observation)
    if before_surface and after_surface:
        signals["selected_object_surface_changed"] = before_surface != after_surface
        if before_surface == after_surface and expected_page_type not in {
            "",
            "input_focused",
        }:
            signals["same_surface_still_visible"] = True
    if text_match:
        signals["selected_object_match"] = True
    return signals
