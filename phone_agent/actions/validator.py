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

    def __init__(
        self,
        code: str,
        message: str,
        *,
        expected: dict | None = None,
        found: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.expected = expected if isinstance(expected, dict) else None
        self.found = _whitelist_found(found)


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
    "Launch": {"_metadata", "action", "app", "package_candidates"},
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
_FOUND_KEYS = {"field", "type", "range", "value", "mark_id", "action", "app"}


def _private_text_keys() -> set[str]:
    try:
        from phone_agent.graph.context import PRIVATE_CONTEXT_TEXT_KEYS

        return {str(key).casefold() for key in PRIVATE_CONTEXT_TEXT_KEYS}
    except Exception:
        return {
            "text",
            "message",
            "hint",
            "answer",
            "label",
            "title",
            "subtitle",
            "value",
        }


def _redacted_found_value(value: Any) -> dict[str, Any]:
    return {"redacted": True, "length": len(str(value))}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_coordinate(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_is_number(item) for item in value)
    )


def _field_token(value: Any) -> str | list[str] | None:
    if isinstance(value, str):
        return value[:96]
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return [str(item)[:96] for item in value[:20]]
    return None


def _field_is_private(field: Any, private_keys: set[str]) -> bool:
    values = field if isinstance(field, (list, tuple)) else [field]
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.rsplit(".", 1)[-1].split("[", 1)[0].casefold()
        if normalized in private_keys:
            return True
    return False


def _whitelist_found(found: Any) -> dict | None:
    """Return trace-safe C1 ``found`` data for guidance rendering."""

    if not isinstance(found, dict):
        return None
    private_keys = _private_text_keys()
    result: dict[str, Any] = {}
    field_private = _field_is_private(found.get("field"), private_keys)
    for key, value in found.items():
        normalized_key = str(key).casefold()
        if normalized_key in private_keys and key not in _FOUND_KEYS:
            result[str(key)] = _redacted_found_value(value)
            continue
        if key not in _FOUND_KEYS:
            continue
        if key == "field":
            token = _field_token(value)
            if token is not None:
                result[key] = token
        elif key in {"type", "range"}:
            if isinstance(value, str):
                result[key] = value[:96]
        elif key in {"mark_id", "action", "app"}:
            if isinstance(value, str):
                result[key] = value[:128]
            elif value is None:
                result[key] = None
        elif key == "value":
            if value is None or _is_number(value):
                result[key] = value
            elif _is_coordinate(value):
                result[key] = list(value)
            else:
                if field_private:
                    result[key] = _redacted_found_value(value)
                else:
                    result["type"] = type(value).__name__
    return result or None


def validate_action(action: dict[str, Any] | ActionIR) -> dict[str, Any]:
    """Validate canonical action and return a plain dict for graph compatibility."""

    try:
        action_dict = to_action_dict(action)
    except Exception as exc:
        raise ActionValidationError(
            "unsafe_value",
            "action must be a dict-like canonical action",
            expected={"type": "dict"},
            found={"type": type(action).__name__},
        ) from exc
    if not isinstance(action_dict, dict):
        raise ActionValidationError(
            "unsafe_value",
            "action must be a dict-like canonical action",
            expected={"type": "dict"},
            found={"type": type(action_dict).__name__},
        )
    _reject_dangerous_fields(action_dict)

    metadata = action_dict.get("_metadata")
    if metadata == "finish":
        _validate_finish(action_dict)
        return action_dict
    if metadata != "do":
        raise ActionValidationError(
            "invalid_metadata",
            "action metadata must be do or finish",
            expected={"field": "_metadata", "value": "do|finish"},
            found={"field": "_metadata", "value": metadata},
        )

    action_name = action_dict.get("action")
    if not isinstance(action_name, str):
        raise ActionValidationError(
            "missing_field",
            "do.action must be a string",
            expected={"field": "action", "type": "string"},
            found={"field": "action", "value": action_name},
        )
    if action_name not in CANONICAL_ACTIONS:
        raise ActionValidationError(
            "unknown_action",
            f"unknown action: {action_name}",
            expected={"field": "action", "type": "canonical_action"},
            found={"action": action_name},
        )
    if get_tool_capability(action_name) is None:
        raise ActionValidationError(
            "capability_missing",
            f"no capability declaration for action: {action_name}",
            expected={"field": "action", "type": "declared_capability"},
            found={"action": action_name},
        )

    allowed = ALLOWED_FIELDS_BY_ACTION[action_name]
    extras = set(action_dict) - allowed
    if extras:
        raise ActionValidationError(
            "unsafe_value",
            f"unsupported fields for {action_name}: {sorted(extras)}",
            expected={"field": sorted(allowed), "type": "allowed_fields"},
            found={"field": sorted(extras)},
        )

    if action_name in {"Tap", "Double Tap", "Long Press"}:
        _require_point(action_dict, "element")
        if "message" in action_dict and not isinstance(action_dict["message"], str):
            raise ActionValidationError(
                "unsafe_value",
                "message must be a string",
                expected={"field": "message", "type": "string"},
                found={"field": "message", "value": action_dict.get("message")},
            )
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
            raise ActionValidationError(
                "missing_field",
                "Locate target_text_hint must be non-empty",
                expected={"field": "target_text_hint", "type": "non_empty_string"},
                found={"field": "target_text_hint", "value": action_dict.get("target_text_hint")},
            )
        if len(hint) > 240:
            raise ActionValidationError(
                "unsafe_value",
                "Locate target_text_hint must be <= 240 characters",
                expected={"field": "target_text_hint", "range": "1..240 chars"},
                found={"field": "target_text_hint", "value": action_dict.get("target_text_hint")},
            )
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
                expected={"field": "scope", "type": "one_of"},
                found={"field": ["scope_mark_id", "scope_start_mark_id"]},
            )
        if scope_mark_id is None and scope_start_mark_id is None:
            raise ActionValidationError(
                "missing_field",
                "Locate requires scope_mark_id or scope_start_mark_id",
                expected={"field": ["scope_mark_id", "scope_start_mark_id"], "type": "mark_id"},
                found={"field": "scope", "value": None},
            )
        if scope_end_mark_id is not None and scope_start_mark_id is None:
            raise ActionValidationError(
                "missing_field",
                "Locate scope_end_mark_id requires scope_start_mark_id",
                expected={"field": "scope_start_mark_id", "type": "mark_id"},
                found={"field": "scope_end_mark_id", "mark_id": scope_end_mark_id},
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
        package_candidates = action_dict.get("package_candidates")
        if package_candidates is not None:
            action_dict["package_candidates"] = _validate_package_candidates(
                package_candidates
            )
        canonical_app = normalize_app_name(action_dict["app"])
        # An app outside the static registry is allowed when the model supplies
        # candidate package hints; the device inventory path resolves it at
        # execution time. Without candidates the previous fail-closed behavior
        # is unchanged.
        if canonical_app is None and not action_dict.get("package_candidates"):
            raise ActionValidationError(
                "unknown_app",
                f"unknown app: {action_dict['app']}",
                expected={"field": "app", "type": "known_app_or_package_candidates"},
                found={"app": action_dict.get("app")},
            )
        if canonical_app is not None:
            action_dict["app"] = canonical_app
    elif action_name == "Wait":
        _require_str(action_dict, "duration")
        _validate_wait_duration(action_dict["duration"])

    return action_dict


def _validate_finish(action: dict[str, Any]) -> None:
    allowed = {"_metadata", "message", "matched_terminal_evidence"}
    extras = set(action) - allowed
    if extras:
        raise ActionValidationError(
            "unsafe_value",
            f"unsupported finish fields: {sorted(extras)}",
            expected={"field": sorted(allowed), "type": "allowed_fields"},
            found={"field": sorted(extras)},
        )
    _require_str(action, "message")
    evidence = action.get("matched_terminal_evidence")
    if evidence is not None:
        if not isinstance(evidence, list):
            raise ActionValidationError(
                "unsafe_value",
                "matched_terminal_evidence must be a list",
                expected={"field": "matched_terminal_evidence", "type": "list"},
                found={"field": "matched_terminal_evidence", "type": type(evidence).__name__},
            )
        for item in evidence:
            if not isinstance(item, str) or not item.strip():
                raise ActionValidationError(
                    "unsafe_value",
                    "matched_terminal_evidence items must be non-empty strings",
                    expected={"field": "matched_terminal_evidence[]", "type": "non_empty_string"},
                    found={"field": "matched_terminal_evidence[]", "value": item},
                )


def _validate_scope_mark_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ActionValidationError(
            "unsafe_value",
            f"Locate {field} must be a string",
            expected={"field": field, "type": "string"},
            found={"field": field, "value": value},
        )
    value = value.strip()
    if not value:
        raise ActionValidationError(
            "missing_field",
            f"Locate {field} must be non-empty",
            expected={"field": field, "type": "non_empty_string"},
            found={"field": field, "value": value},
        )
    if not SAFE_MARK_ID_RE.fullmatch(value):
        raise ActionValidationError(
            "unsafe_value",
            f"Locate {field} contains unsafe characters",
            expected={"field": field, "type": "safe_mark_id"},
            found={"field": field, "mark_id": value},
        )
    return value


def _reject_dangerous_fields(action: dict[str, Any]) -> None:
    dangerous = {key for key in action if key.lower() in DANGEROUS_FIELDS}
    if dangerous:
        raise ActionValidationError(
            "unsafe_value",
            f"dangerous fields are not allowed: {sorted(dangerous)}",
            expected={"field": "provider_fields", "type": "safe_fields"},
            found={"field": sorted(dangerous)},
        )


def _require_str(action: dict[str, Any], field: str) -> None:
    if not isinstance(action.get(field), str):
        raise ActionValidationError(
            "missing_field",
            f"{field} must be a string",
            expected={"field": field, "type": "string"},
            found={"field": field, "value": action.get(field)},
        )


MAX_PACKAGE_CANDIDATES = 20


def _validate_package_candidates(value: Any) -> list[str]:
    """Validate and normalize the optional Launch candidate package list."""

    if not isinstance(value, list):
        raise ActionValidationError(
            "unsafe_value",
            "Launch package_candidates must be a list",
            expected={"field": "package_candidates", "type": "list"},
            found={"field": "package_candidates", "type": type(value).__name__},
        )
    if not value:
        raise ActionValidationError(
            "unsafe_value",
            "Launch package_candidates must be non-empty when provided",
            expected={"field": "package_candidates", "type": "non_empty_list"},
            found={"field": "package_candidates", "value": None},
        )
    if len(value) > MAX_PACKAGE_CANDIDATES:
        raise ActionValidationError(
            "unsafe_value",
            f"Launch package_candidates must have <= {MAX_PACKAGE_CANDIDATES} items",
            expected={"field": "package_candidates", "range": f"1..{MAX_PACKAGE_CANDIDATES} items"},
            found={"field": "package_candidates", "value": len(value)},
        )
    candidates: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ActionValidationError(
                "unsafe_value",
                "Launch package_candidates items must be strings",
                expected={"field": "package_candidates[]", "type": "string"},
                found={"field": "package_candidates[]", "value": item},
            )
        stripped = item.strip()
        if not stripped:
            raise ActionValidationError(
                "unsafe_value",
                "Launch package_candidates items must be non-empty strings",
                expected={"field": "package_candidates[]", "type": "non_empty_string"},
                found={"field": "package_candidates[]", "value": item},
            )
        candidates.append(stripped)
    return candidates


def _require_point(action: dict[str, Any], field: str) -> None:
    value = action.get(field)
    if not isinstance(value, list) or len(value) != 2:
        raise ActionValidationError(
            "missing_field",
            f"{field} must be [x, y]",
            expected={"field": field, "type": "[x, y]"},
            found={"field": field, "value": value},
        )
    for index, coordinate in enumerate(value):
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            raise ActionValidationError(
                "unsafe_value",
                "coordinates must be numeric",
                expected={"field": f"{field}[{index}]", "type": "number"},
                found={"field": f"{field}[{index}]", "value": coordinate},
            )
        if coordinate < 0 or coordinate > 1000:
            raise ActionValidationError(
                "unsafe_value",
                "coordinates must be in 0-1000",
                expected={"field": f"{field}[{index}]", "range": "0..1000"},
                found={"field": f"{field}[{index}]", "value": coordinate},
            )


def _validate_wait_duration(duration: str) -> None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*seconds?\s*", duration)
    if not match:
        raise ActionValidationError(
            "unsafe_value",
            "Wait duration must be '<seconds> seconds'",
            expected={"field": "duration", "type": "<seconds> seconds"},
            found={"field": "duration", "type": type(duration).__name__},
        )
    seconds = float(match.group(1))
    if seconds <= 0:
        raise ActionValidationError(
            "unsafe_value",
            "Wait duration must be positive",
            expected={"field": "duration", "range": "0..60 seconds"},
            found={"field": "duration", "value": seconds},
        )
    if seconds > MAX_WAIT_SECONDS:
        raise ActionValidationError(
            "unsafe_value",
            f"Wait duration must be <= {MAX_WAIT_SECONDS:g} seconds",
            expected={"field": "duration", "range": f"0..{MAX_WAIT_SECONDS:g} seconds"},
            found={"field": "duration", "value": seconds},
        )
