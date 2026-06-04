"""Harness-side grounding from IntentIR to canonical executable ActionIR."""

from __future__ import annotations

from typing import Any

from phone_agent.actions.adapter import ActionAdapterError, _canonical_action_name
from phone_agent.actions.ir import is_intent_dict
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.graph.marks import MarkRegistry, SAFE_MARK_ID_RE
from phone_agent.grounding.provider import GroundingProvider, GroundingResult, GroundingTarget, ScreenBinding


class GroundingError(ValueError):
    """Grounding failure with stable code for trace/eval."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


INTENT_ALLOWED_FIELDS = {
    "_metadata",
    "action",
    "target_mark_id",
    "target_role",
    "target_text_hint",
    "requires_grounding",
    "target_intent",
    "text",
    "message",
    "app",
    "duration",
}

CONFIRM_TERMS = {
    "pay",
    "payment",
    "purchase",
    "buy",
    "order",
    "confirm",
    "delete",
    "remove",
    "permission",
    "privacy",
    "支付",
    "付款",
    "购买",
    "下单",
    "确认",
    "删除",
    "移除",
    "权限",
    "隐私",
}

TAKEOVER_TERMS = {
    "login",
    "password",
    "captcha",
    "otp",
    "code",
    "account",
    "登录",
    "密码",
    "验证码",
    "账户",
    "账号",
}


def validate_intent(intent: dict[str, Any]) -> dict[str, Any]:
    if not is_intent_dict(intent):
        raise GroundingError("invalid_intent", "intent metadata must be intent")
    extras = set(intent) - INTENT_ALLOWED_FIELDS
    if extras:
        raise GroundingError("unsafe_value", f"unsupported intent fields: {sorted(extras)}")
    if "target_mark_id" in intent:
        if not isinstance(intent["target_mark_id"], str):
            raise GroundingError("unsafe_value", "target_mark_id must be a string")
        if not SAFE_MARK_ID_RE.fullmatch(intent["target_mark_id"]):
            raise GroundingError("unsafe_value", "target_mark_id contains unsafe characters")
    for key in ("target_role", "target_text_hint", "target_intent", "text", "message", "app", "duration"):
        if key in intent and not isinstance(intent[key], str):
            raise GroundingError("unsafe_value", f"{key} must be a string")
    if "requires_grounding" in intent and not isinstance(intent["requires_grounding"], bool):
        raise GroundingError("unsafe_value", "requires_grounding must be a boolean")
    action = intent.get("action") or intent.get("target_intent")
    if action is not None:
        try:
            intent["action"] = _canonical_action_name(action)
        except ActionAdapterError as exc:
            raise GroundingError("unknown_action", str(exc)) from exc
    return intent


def ground_intent_to_action(
    intent: dict[str, Any], *, mark_registry: MarkRegistry | dict[str, Any] | None, screen_id: str | None,
    grounding_provider: GroundingProvider | None = None, screenshot: Any | None = None,
    screen_binding: ScreenBinding | None = None, timeout: float | None = None,
    grounding_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a validated IntentIR dict into canonical ActionIR dict."""

    intent = validate_intent(dict(intent))
    registry = mark_registry if isinstance(mark_registry, MarkRegistry) else MarkRegistry.from_dict(mark_registry)
    action_name = intent.get("action") or intent.get("target_intent")
    if not action_name:
        raise GroundingError("missing_field", "intent requires action or target_intent")
    action_name = _canonical_action_name(action_name)

    mark_id = intent.get("target_mark_id")
    if mark_id:
        if registry is None or not registry.marks:
            raise GroundingError("mark_unavailable", "no MarkRegistry available for mark intent")
        if screen_id and registry.screen_id != screen_id:
            raise GroundingError("stale_mark", "MarkRegistry screen_id does not match current screen")
        mark = registry.get(mark_id)
        if mark is None:
            raise GroundingError("unknown_mark", f"unknown mark: {mark_id}")
        if screen_id and mark.screen_id != screen_id:
            raise GroundingError("stale_mark", "mark belongs to another screen")
        sensitivity = _mark_sensitivity(intent, mark)
        if sensitivity == "takeover":
            try:
                return validate_action(
                    {
                        "_metadata": "do",
                        "action": "Take_over",
                        "message": "Sensitive mark-grounded action requires takeover",
                    }
                )
            except ActionValidationError as exc:
                raise GroundingError(exc.code, str(exc)) from exc
        if action_name not in {"Tap", "Double Tap", "Long Press"}:
            raise GroundingError("unsupported_intent", "mark grounding currently supports tap-like actions only")
        action: dict[str, Any] = {"_metadata": "do", "action": action_name, "element": [mark.center[0], mark.center[1]]}
        if grounding_metadata is not None:
            grounding_metadata.update(
                {
                    "success": True,
                    "provider": "mark_registry",
                    "screen_id": screen_id,
                    "bbox": list(mark.bbox),
                    "center": [mark.center[0], mark.center[1]],
                    "target": {"mark_id": mark_id},
                }
            )
        if sensitivity == "confirm":
            action["message"] = "Sensitive mark-grounded tap requires confirmation"
        elif "message" in intent:
            action["message"] = intent["message"]
        try:
            return validate_action(action)
        except ActionValidationError as exc:
            raise GroundingError(exc.code, str(exc)) from exc

    if _has_description_target(intent):
        return _ground_description_intent(
            intent,
            action_name=action_name,
            grounding_provider=grounding_provider,
            screenshot=screenshot,
            screen_binding=screen_binding,
            timeout=timeout,
            grounding_metadata=grounding_metadata,
        )

    if not intent.get("requires_grounding", True):
        return _ground_non_target_intent(intent, action_name)

    raise GroundingError("target_required", "intent grounding requires target_mark_id or target description")


def _has_description_target(intent: dict[str, Any]) -> bool:
    return any(bool(intent.get(key)) for key in ("target_text_hint", "target_role", "target_intent"))


def _ground_description_intent(
    intent: dict[str, Any], *, action_name: str,
    grounding_provider: GroundingProvider | None,
    screenshot: Any | None,
    screen_binding: ScreenBinding | None,
    timeout: float | None,
    grounding_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if action_name not in {"Tap", "Double Tap", "Long Press"}:
        raise GroundingError("unsupported_intent", "description grounding supports tap-like actions only")
    if grounding_provider is None:
        raise GroundingError("provider_unavailable", "no GroundingProvider available for description intent")
    if screenshot is None:
        raise GroundingError("screenshot_unavailable", "screenshot is required for description grounding")
    if screen_binding is None:
        raise GroundingError("screen_binding_missing", "screen binding is required for description grounding")
    target = GroundingTarget(
        text_hint=intent.get("target_text_hint"),
        role=intent.get("target_role"),
        intent=intent.get("target_intent"),
        action=action_name,
        requires_grounding=bool(intent.get("requires_grounding", True)),
    )
    result = grounding_provider.ground(screenshot, target, screen_binding, timeout)
    if grounding_metadata is not None:
        grounding_metadata.update(result.to_dict())
        grounding_metadata["target"] = target.redacted_summary()
    _validate_grounding_result(result, screen_binding)
    sensitivity = _description_sensitivity(intent)
    if sensitivity == "takeover":
        return validate_action(
            {"_metadata": "do", "action": "Take_over", "message": "Sensitive grounded action requires takeover"}
        )
    action: dict[str, Any] = {
        "_metadata": "do",
        "action": action_name,
        "element": list(result.center or []),
    }
    if sensitivity == "confirm":
        action["message"] = "Sensitive grounded tap requires confirmation"
    elif "message" in intent:
        action["message"] = intent["message"]
    try:
        return validate_action(action)
    except ActionValidationError as exc:
        raise GroundingError(exc.code, str(exc)) from exc


def _validate_grounding_result(result: GroundingResult, binding: ScreenBinding) -> None:
    if not result.success:
        raise GroundingError(
            result.failure_code or "provider_failure",
            result.message or "grounding provider failed",
        )
    if result.screen_id != binding.screen_id:
        raise GroundingError("stale_screen", "grounding result screen_id does not match current screen")
    if result.raw_screenshot_hash != binding.raw_screenshot_hash:
        raise GroundingError("hash_mismatch", "grounding result screenshot hash does not match current screen")
    if not result.provider_input_hash:
        raise GroundingError("missing_provider_hash", "grounding result missing provider input image hash")
    if not result.center or len(result.center) != 2:
        raise GroundingError("bad_bbox", "grounding result missing center")
    if result.bbox is not None and len(result.bbox) != 4:
        raise GroundingError("bad_bbox", "grounding result bbox must have four values")
    if result.confidence is not None and result.confidence < 0.3:
        raise GroundingError("low_confidence", "grounding confidence is below threshold")


def _ground_non_target_intent(intent: dict[str, Any], action_name: str) -> dict[str, Any]:
    action: dict[str, Any] = {"_metadata": "do", "action": action_name}
    if action_name in {"Back", "Home"}:
        return validate_action(action)
    if action_name == "Launch" and intent.get("app"):
        action["app"] = intent["app"]
        return validate_action(action)
    if action_name == "Wait" and intent.get("duration"):
        action["duration"] = intent["duration"]
        return validate_action(action)
    if action_name in {"Type", "Type_Name"} and intent.get("text"):
        action["text"] = intent["text"]
        return validate_action(action)
    raise GroundingError("unsupported_intent", "non-target intent is missing required fields")


def _mark_sensitivity(intent: dict[str, Any], mark: Any) -> str | None:
    haystack = " ".join(
        str(value or "")
        for value in (
            intent.get("target_role"),
            intent.get("target_text_hint"),
            intent.get("target_intent"),
            intent.get("message"),
            getattr(mark, "role", None),
            getattr(mark, "text_summary", None),
        )
    ).lower()
    if any(term.lower() in haystack for term in TAKEOVER_TERMS):
        return "takeover"
    if any(term.lower() in haystack for term in CONFIRM_TERMS):
        return "confirm"
    return None


def _description_sensitivity(intent: dict[str, Any]) -> str | None:
    haystack = " ".join(str(intent.get(key) or "") for key in ("target_role", "target_text_hint", "target_intent", "message")).lower()
    if any(term.lower() in haystack for term in TAKEOVER_TERMS):
        return "takeover"
    if any(term.lower() in haystack for term in CONFIRM_TERMS):
        return "confirm"
    return None
