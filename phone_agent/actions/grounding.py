"""Harness-side grounding from IntentIR to canonical executable ActionIR."""

from __future__ import annotations

from typing import Any

from phone_agent.actions.adapter import _canonical_action_name
from phone_agent.actions.ir import is_intent_dict
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.graph.marks import MarkRegistry, SAFE_MARK_ID_RE


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
    action = intent.get("action") or intent.get("target_intent")
    if action is not None:
        try:
            intent["action"] = _canonical_action_name(action)
        except Exception as exc:
            raise GroundingError("unknown_action", str(exc)) from exc
    return intent


def ground_intent_to_action(
    intent: dict[str, Any], *, mark_registry: MarkRegistry | dict[str, Any] | None, screen_id: str | None
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
        if sensitivity == "confirm":
            action["message"] = "Sensitive mark-grounded tap requires confirmation"
        elif "message" in intent:
            action["message"] = intent["message"]
        try:
            return validate_action(action)
        except ActionValidationError as exc:
            raise GroundingError(exc.code, str(exc)) from exc

    raise GroundingError("mark_required", "intent grounding requires target_mark_id")


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
