"""Canonical action validation.

All provider paths must pass through this module before safety or execution.
Validation is semantic/schema/range oriented and does not execute tools.
"""

from __future__ import annotations

import re
from typing import Any

from phone_agent.actions.constants import BASE_DANGEROUS_FIELDS
from phone_agent.actions.capability import get_tool_capability
from phone_agent.actions.ir import ActionIR, to_action_dict
from phone_agent.graph.marks import SAFE_MARK_ID_RE


class ActionValidationError(ValueError):
    """Validation error with stable code for trace/eval metadata."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DANGEROUS_FIELDS = BASE_DANGEROUS_FIELDS

ALLOWED_FIELDS_BY_ACTION: dict[str, set[str]] = {
    "Tap": {"_metadata", "action", "element", "message"},
    "Double Tap": {"_metadata", "action", "element"},
    "Long Press": {"_metadata", "action", "element"},
    "Swipe": {"_metadata", "action", "start", "end"},
    "Type": {"_metadata", "action", "text"},
    "Type_Name": {"_metadata", "action", "text"},
    "Back": {"_metadata", "action"},
    "Home": {"_metadata", "action"},
    "Launch": {"_metadata", "action", "app"},
    "Wait": {"_metadata", "action", "duration"},
    "Note": {"_metadata", "action", "message"},
    "Call_API": {"_metadata", "action", "message"},
    "Interact": {"_metadata", "action", "message"},
    "Locate": {
        "_metadata",
        "action",
        "target_text_hint",
        "scope_mark_id",
        "scope_start_mark_id",
        "scope_end_mark_id",
    },
    "Take_over": {"_metadata", "action", "message"},
}
CANONICAL_ACTIONS = set(ALLOWED_FIELDS_BY_ACTION)
MAX_WAIT_SECONDS = 60.0


def validate_action(action: dict[str, Any] | ActionIR) -> dict[str, Any]:
    """Validate canonical action and return a plain dict for graph compatibility."""

    try:
        action_dict = to_action_dict(action)
    except Exception as exc:
        raise ActionValidationError("unsafe_value", "action must be a dict-like canonical action") from exc
    if not isinstance(action_dict, dict):
        raise ActionValidationError("unsafe_value", "action must be a dict-like canonical action")
    _reject_dangerous_fields(action_dict)

    metadata = action_dict.get("_metadata")
    if metadata == "finish":
        _validate_finish(action_dict)
        return action_dict
    if metadata != "do":
        raise ActionValidationError("invalid_metadata", "action metadata must be do or finish")

    action_name = action_dict.get("action")
    if not isinstance(action_name, str):
        raise ActionValidationError("missing_field", "do.action must be a string")
    if action_name not in CANONICAL_ACTIONS:
        raise ActionValidationError("unknown_action", f"unknown action: {action_name}")
    if get_tool_capability(action_name) is None:
        raise ActionValidationError(
            "capability_missing", f"no capability declaration for action: {action_name}"
        )

    allowed = ALLOWED_FIELDS_BY_ACTION[action_name]
    extras = set(action_dict) - allowed
    if extras:
        raise ActionValidationError(
            "unsafe_value", f"unsupported fields for {action_name}: {sorted(extras)}"
        )

    if action_name in {"Tap", "Double Tap", "Long Press"}:
        _require_point(action_dict, "element")
        if "message" in action_dict and not isinstance(action_dict["message"], str):
            raise ActionValidationError("unsafe_value", "message must be a string")
    elif action_name == "Swipe":
        _require_point(action_dict, "start")
        _require_point(action_dict, "end")
    elif action_name in {"Type", "Type_Name"}:
        _require_str(action_dict, "text")
    elif action_name in {"Note", "Call_API", "Interact", "Take_over"}:
        _require_str(action_dict, "message")
    elif action_name == "Locate":
        _require_str(action_dict, "target_text_hint")
        hint = action_dict["target_text_hint"].strip()
        if not hint:
            raise ActionValidationError("missing_field", "Locate target_text_hint must be non-empty")
        if len(hint) > 240:
            raise ActionValidationError("unsafe_value", "Locate target_text_hint must be <= 240 characters")
        scope_mark_id = action_dict.get("scope_mark_id")
        scope_start_mark_id = action_dict.get("scope_start_mark_id")
        scope_end_mark_id = action_dict.get("scope_end_mark_id")
        # P1: scope is mandatory. Exactly one of the two forms must be present:
        #  form A = scope_mark_id (single container);
        #  form B = scope_start_mark_id (+ optional scope_end_mark_id) interval.
        if scope_mark_id is not None and scope_start_mark_id is not None:
            raise ActionValidationError(
                "unsafe_value",
                "Locate accepts either scope_mark_id or scope_start_mark_id, not both",
            )
        if scope_mark_id is None and scope_start_mark_id is None:
            raise ActionValidationError(
                "missing_field",
                "Locate requires scope_mark_id or scope_start_mark_id",
            )
        if scope_end_mark_id is not None and scope_start_mark_id is None:
            raise ActionValidationError(
                "missing_field",
                "Locate scope_end_mark_id requires scope_start_mark_id",
            )
        if scope_mark_id is not None:
            action_dict["scope_mark_id"] = _validate_scope_mark_id(
                scope_mark_id, "scope_mark_id"
            )
        else:
            action_dict["scope_start_mark_id"] = _validate_scope_mark_id(
                scope_start_mark_id, "scope_start_mark_id"
            )
            if scope_end_mark_id is not None:
                action_dict["scope_end_mark_id"] = _validate_scope_mark_id(
                    scope_end_mark_id, "scope_end_mark_id"
                )
    elif action_name == "Launch":
        _require_str(action_dict, "app")
        from phone_agent.config.apps import normalize_app_name
        canonical_app = normalize_app_name(action_dict["app"])
        if canonical_app is None:
            raise ActionValidationError("unknown_app", f"unknown app: {action_dict['app']}")
        action_dict["app"] = canonical_app
    elif action_name == "Wait":
        _require_str(action_dict, "duration")
        _validate_wait_duration(action_dict["duration"])

    return action_dict


def _validate_finish(action: dict[str, Any]) -> None:
    allowed = {"_metadata", "message", "matched_terminal_evidence"}
    extras = set(action) - allowed
    if extras:
        raise ActionValidationError("unsafe_value", f"unsupported finish fields: {sorted(extras)}")
    _require_str(action, "message")
    evidence = action.get("matched_terminal_evidence")
    if evidence is not None:
        if not isinstance(evidence, list):
            raise ActionValidationError("unsafe_value", "matched_terminal_evidence must be a list")
        for item in evidence:
            if not isinstance(item, str) or not item.strip():
                raise ActionValidationError(
                    "unsafe_value", "matched_terminal_evidence items must be non-empty strings"
                )


def _validate_scope_mark_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ActionValidationError("unsafe_value", f"Locate {field} must be a string")
    value = value.strip()
    if not value:
        raise ActionValidationError("missing_field", f"Locate {field} must be non-empty")
    if not SAFE_MARK_ID_RE.fullmatch(value):
        raise ActionValidationError("unsafe_value", f"Locate {field} contains unsafe characters")
    return value


def _reject_dangerous_fields(action: dict[str, Any]) -> None:
    dangerous = {key for key in action if key.lower() in DANGEROUS_FIELDS}
    if dangerous:
        raise ActionValidationError("unsafe_value", f"dangerous fields are not allowed: {sorted(dangerous)}")


def _require_str(action: dict[str, Any], field: str) -> None:
    if not isinstance(action.get(field), str):
        raise ActionValidationError("missing_field", f"{field} must be a string")


def _require_point(action: dict[str, Any], field: str) -> None:
    value = action.get(field)
    if not isinstance(value, list) or len(value) != 2:
        raise ActionValidationError("missing_field", f"{field} must be [x, y]")
    for coordinate in value:
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            raise ActionValidationError("unsafe_value", "coordinates must be numeric")
        if coordinate < 0 or coordinate > 1000:
            raise ActionValidationError("unsafe_value", "coordinates must be in 0-1000")


def _validate_wait_duration(duration: str) -> None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*seconds?\s*", duration)
    if not match:
        raise ActionValidationError("unsafe_value", "Wait duration must be '<seconds> seconds'")
    seconds = float(match.group(1))
    if seconds <= 0:
        raise ActionValidationError("unsafe_value", "Wait duration must be positive")
    if seconds > MAX_WAIT_SECONDS:
        raise ActionValidationError(
            "unsafe_value", f"Wait duration must be <= {MAX_WAIT_SECONDS:g} seconds"
        )
