"""Provider action format adapters.

Adapters convert provider-facing JSON/tool_calls into the internal canonical
action dictionary consumed by the existing graph execution path. They never
execute tools and fail closed on unknown or unsafe input.
"""

from __future__ import annotations

import json
from typing import Any

from phone_agent.actions.constants import BASE_DANGEROUS_FIELDS
from phone_agent.actions.selectors import validate_object_filter
from phone_agent.actions.validator import _whitelist_found


class ActionAdapterError(ValueError):
    """Adapter error with a stable machine-readable error code."""

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


ACTION_ALIASES = {
    "tap": "Tap",
    "double_tap": "Double Tap",
    "double tap": "Double Tap",
    "long_press": "Long Press",
    "long press": "Long Press",
    "swipe": "Swipe",
    "type": "Type",
    "type_name": "Type_Name",
    "back": "Back",
    "home": "Home",
    "launch": "Launch",
    "wait": "Wait",
    "note": "Note",
    "call_api": "Call_API",
    "interact": "Interact",
    "locate": "Locate",
    "take_over": "Take_over",
}
CANONICAL_ACTIONS = set(ACTION_ALIASES.values())
TOOL_NAME_ALIASES = {"do", "finish", "phone_do", "phone_finish"}
ALLOWED_TOOL_CALL_FIELDS = {"id", "type", "function", "index"}
ALLOWED_TOOL_FUNCTION_FIELDS = {"name", "arguments"}
DANGEROUS_PROVIDER_FIELDS = BASE_DANGEROUS_FIELDS | {
    "backend",
    "model_path",
}
COMMON_DO_FIELDS = {"type", "_metadata", "action", "message"}
INTENT_FIELDS = {
    "type",
    "_metadata",
    "action",
    "target_mark_id",
    "target_object_id",
    "ordinal",
    "object_role",
    "object_filter",
    "target_role",
    "target_text_hint",
    "scope_mark_id",
    "scope_start_mark_id",
    "scope_end_mark_id",
    "target_text",
    "requires_grounding",
    "text",
    "message",
    "app",
    "duration",
}
ALLOWED_PROVIDER_FIELDS_BY_ACTION: dict[str, set[str]] = {
    "Tap": COMMON_DO_FIELDS | {"element", "x", "y"},
    "Double Tap": COMMON_DO_FIELDS | {"element", "x", "y"},
    "Long Press": COMMON_DO_FIELDS | {"element", "x", "y"},
    "Swipe": COMMON_DO_FIELDS | {"start", "start_element", "end", "end_element"},
    "Type": COMMON_DO_FIELDS | {"text"},
    "Type_Name": COMMON_DO_FIELDS | {"text"},
    "Back": COMMON_DO_FIELDS,
    "Home": COMMON_DO_FIELDS,
    "Launch": COMMON_DO_FIELDS | {"app", "app_name", "package_candidates"},
    "Wait": COMMON_DO_FIELDS | {"duration"},
    "Note": COMMON_DO_FIELDS | {"text"},
    "Call_API": COMMON_DO_FIELDS | {"text"},
    "Interact": COMMON_DO_FIELDS | {"text"},
    "Locate": COMMON_DO_FIELDS
    | {"target_text_hint", "scope_mark_id", "scope_start_mark_id", "scope_end_mark_id"},
    "Take_over": COMMON_DO_FIELDS | {"text"},
}


def adapt_json_action(payload: str | dict[str, Any]) -> dict[str, Any]:
    """Adapt provider-facing JSON into an internal canonical action dict."""
    data = _coerce_json_object(payload)
    _reject_dangerous_provider_fields(data)
    action_type = data.get("type") or data.get("_metadata")
    intent_keys = {
        "target_mark_id",
        "target_object_id",
        "ordinal",
        "object_role",
        "object_filter",
        "target_text_hint",
        "target_text",
        "target_role",
        "requires_grounding",
    }
    if action_type == "intent" or bool(intent_keys & set(data)):
        _reject_unexpected_provider_fields(data, INTENT_FIELDS)
        intent: dict[str, Any] = {"_metadata": "intent"}
        for key in INTENT_FIELDS - {"type", "_metadata"}:
            if key in data:
                value = data[key]
                if key == "target_text":
                    key = "target_text_hint"
                if key == "requires_grounding":
                    if not isinstance(value, bool):
                        raise ActionAdapterError(
                            "unsafe_value",
                            "requires_grounding must be a boolean",
                            expected={"field": "requires_grounding", "type": "boolean"},
                            found={"field": "requires_grounding", "value": value},
                        )
                elif key in {
                    "action",
                    "target_mark_id",
                    "target_object_id",
                    "object_role",
                    "target_role",
                    "target_text_hint",
                    "scope_mark_id",
                    "scope_start_mark_id",
                    "scope_end_mark_id",
                    "text",
                    "message",
                    "app",
                    "duration",
                } and not isinstance(value, str):
                    raise ActionAdapterError(
                        "unsafe_value",
                        f"{key} must be a string",
                        expected={"field": key, "type": "string"},
                        found={"field": key, "value": value},
                    )
                elif key == "ordinal":
                    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > 100:
                        raise ActionAdapterError(
                            "unsafe_value",
                            "ordinal must be a positive integer <= 100",
                            expected={"field": "ordinal", "range": "1..100"},
                            found={"field": "ordinal", "value": value},
                        )
                elif key == "object_filter":
                    try:
                        value = validate_object_filter(value)
                    except ValueError as exc:
                        raise ActionAdapterError(
                            "unsafe_value",
                            str(exc),
                            expected={"field": "object_filter", "type": "safe_object_filter"},
                            found={"field": "object_filter", "type": type(value).__name__},
                        ) from exc
                intent[key] = value
        if "action" not in intent:
            raise ActionAdapterError(
                "missing_field",
                "intent requires action",
                expected={"field": "action", "type": "string"},
                found={"field": "action", "value": None},
            )
        intent["action"] = _canonical_action_name(intent["action"])
        if intent["action"] in {"Tap", "Double Tap", "Long Press"} and "target_mark_id" not in intent:
            if not _has_object_selector(intent):
                raise ActionAdapterError(
                    "mark_required",
                    "tap-like intent requires target_mark_id or object selector",
                    expected={"field": "target_mark_id", "type": "mark_id_or_object_selector"},
                    found={"field": "target_mark_id", "value": None},
                )
        return intent
    if action_type == "finish":
        _reject_unexpected_provider_fields(data, {"type", "_metadata", "message", "matched_terminal_evidence"})
        message = data.get("message")
        if not isinstance(message, str):
            raise ActionAdapterError(
                "missing_field",
                "finish.message must be a string",
                expected={"field": "message", "type": "string"},
                found={"field": "message", "value": message},
            )
        action: dict[str, Any] = {"_metadata": "finish", "message": message}
        raw_evidence = data.get("matched_terminal_evidence")
        if raw_evidence is not None:
            if not isinstance(raw_evidence, list):
                raise ActionAdapterError(
                    "unsafe_value",
                    "matched_terminal_evidence must be a list",
                    expected={"field": "matched_terminal_evidence", "type": "list"},
                    found={"field": "matched_terminal_evidence", "type": type(raw_evidence).__name__},
                )
            for item in raw_evidence:
                if not isinstance(item, str) or not item.strip():
                    raise ActionAdapterError(
                        "unsafe_value",
                        "matched_terminal_evidence items must be non-empty strings",
                        expected={"field": "matched_terminal_evidence[]", "type": "non_empty_string"},
                        found={"field": "matched_terminal_evidence[]", "value": item},
                    )
            action["matched_terminal_evidence"] = [item.strip() for item in raw_evidence]
        return action
    if action_type != "do":
        raise ActionAdapterError(
            "unknown_action",
            "action type must be do or finish",
            expected={"field": "type", "value": "do|finish|intent"},
            found={"field": "type", "value": action_type},
        )

    action_name = _canonical_action_name(data.get("action"))
    if action_name in {"Tap", "Double Tap", "Long Press"} and bool(intent_keys & set(data)):
        intent_payload = {
            key: value
            for key, value in data.items()
            if key in (INTENT_FIELDS | {"type"})
        }
        intent_payload["type"] = "intent"
        intent_payload["action"] = action_name
        return adapt_json_action(intent_payload)
    _reject_unexpected_provider_fields(data, ALLOWED_PROVIDER_FIELDS_BY_ACTION[action_name])
    if action_name in {"Tap", "Double Tap", "Long Press"}:
        raise ActionAdapterError(
            "mark_required",
            "tap-like actions must use intent target_mark_id or object selector",
            expected={"field": "target_mark_id", "type": "mark_id_or_object_selector"},
            found={"action": action_name},
        )
    action: dict[str, Any] = {"_metadata": "do", "action": action_name}
    if "message" in data:
        if not isinstance(data["message"], str):
            raise ActionAdapterError(
                "unsafe_value",
                "message must be a string",
                expected={"field": "message", "type": "string"},
                found={"field": "message", "value": data.get("message")},
            )
        action["message"] = data["message"]

    if action_name == "Swipe":
        start = data.get("start") or data.get("start_element")
        end = data.get("end") or data.get("end_element")
        if start is None or end is None:
            raise ActionAdapterError(
                "missing_field",
                "swipe requires start and end",
                expected={"field": ["start", "end"], "type": "[x, y]"},
                found={"field": "start/end", "value": None},
            )
        action["start"] = _validate_point(start, "start")
        action["end"] = _validate_point(end, "end")
    elif action_name in {"Type", "Type_Name", "Note", "Call_API", "Interact", "Take_over"}:
        text_value = data.get("text", data.get("message"))
        if not isinstance(text_value, str):
            raise ActionAdapterError(
                "missing_field",
                f"{action_name} requires text/message",
                expected={"field": "text", "type": "string"},
                found={"field": "text", "value": text_value},
            )
        if action_name in {"Take_over", "Note", "Call_API", "Interact"}:
            action["message"] = text_value
        else:
            action["text"] = text_value
    elif action_name == "Locate":
        hint = data.get("target_text_hint")
        if not isinstance(hint, str) or not hint.strip():
            raise ActionAdapterError(
                "missing_field",
                "Locate requires target_text_hint",
                expected={"field": "target_text_hint", "type": "non_empty_string"},
                found={"field": "target_text_hint", "value": hint},
            )
        action["target_text_hint"] = hint
        # P1: scope fields pass through the intent path too; this copies them
        # for any do-style payload that reaches the do branch (belt and braces).
        for key in ("scope_mark_id", "scope_start_mark_id", "scope_end_mark_id"):
            if key in data:
                value = data[key]
                if not isinstance(value, str):
                    raise ActionAdapterError(
                        "unsafe_value",
                        f"{key} must be a string",
                        expected={"field": key, "type": "string"},
                        found={"field": key, "value": value},
                    )
                action[key] = value
    elif action_name == "Launch":
        app = data.get("app") or data.get("app_name")
        if not isinstance(app, str):
            raise ActionAdapterError(
                "missing_field",
                "Launch requires app",
                expected={"field": "app", "type": "string"},
                found={"field": "app", "value": app},
            )
        action["app"] = app
        if "package_candidates" in data:
            candidates = data["package_candidates"]
            if not isinstance(candidates, list) or not candidates:
                raise ActionAdapterError(
                    "unsafe_value",
                    "package_candidates must be a non-empty list",
                    expected={"field": "package_candidates", "type": "non_empty_list"},
                    found={"field": "package_candidates", "type": type(candidates).__name__},
                )
            for item in candidates:
                if not isinstance(item, str) or not item.strip():
                    raise ActionAdapterError(
                        "unsafe_value",
                        "package_candidates items must be non-empty strings",
                        expected={"field": "package_candidates[]", "type": "non_empty_string"},
                        found={"field": "package_candidates[]", "value": item},
                    )
            action["package_candidates"] = [item.strip() for item in candidates]
    elif action_name == "Wait":
        if "duration" not in data:
            raise ActionAdapterError(
                "missing_field",
                "Wait requires duration",
                expected={"field": "duration", "type": "string|number"},
                found={"field": "duration", "value": None},
            )
        duration = data["duration"]
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            duration = f"{duration} seconds"
        if not isinstance(duration, str):
            raise ActionAdapterError(
                "unsafe_value",
                "Wait duration must be string or number",
                expected={"field": "duration", "type": "string|number"},
                found={"field": "duration", "value": data.get("duration")},
            )
        action["duration"] = duration
    elif action_name not in {"Back", "Home"}:
        raise ActionAdapterError(
            "unknown_action",
            f"unsupported action: {action_name}",
            expected={"field": "action", "type": "supported_action"},
            found={"action": action_name},
        )
    return action


def adapt_tool_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Adapt an already aggregated OpenAI tool_calls list into one action dict."""
    if len(tool_calls) != 1:
        raise ActionAdapterError(
            "unsupported_tool_call",
            "exactly one tool call is supported",
            expected={"field": "tool_calls", "range": "1"},
            found={"field": "tool_calls", "value": len(tool_calls)},
        )
    tool_call = tool_calls[0]
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise ActionAdapterError(
            "unsupported_tool_call",
            "tool call missing function",
            expected={"field": "function", "type": "object"},
            found={"field": "function", "type": type(function).__name__},
        )
    _reject_dangerous_provider_fields(tool_call)
    _reject_dangerous_provider_fields(function)
    _reject_unexpected_provider_fields(tool_call, ALLOWED_TOOL_CALL_FIELDS)
    _reject_unexpected_provider_fields(function, ALLOWED_TOOL_FUNCTION_FIELDS)
    if "type" in tool_call and tool_call["type"] != "function":
        raise ActionAdapterError(
            "unsupported_tool_call",
            "tool call type must be function",
            expected={"field": "type", "value": "function"},
            found={"field": "type", "value": tool_call.get("type")},
        )
    if "id" in tool_call and not isinstance(tool_call["id"], str):
        raise ActionAdapterError(
            "unsupported_tool_call",
            "tool call id must be a string",
            expected={"field": "id", "type": "string"},
            found={"field": "id", "value": tool_call.get("id")},
        )
    if "index" in tool_call and not isinstance(tool_call["index"], int):
        raise ActionAdapterError(
            "unsupported_tool_call",
            "tool call index must be an integer",
            expected={"field": "index", "type": "integer"},
            found={"field": "index", "value": tool_call.get("index")},
        )
    name = function.get("name")
    if not isinstance(name, str) or name.lower() not in TOOL_NAME_ALIASES:
        raise ActionAdapterError(
            "unsupported_tool_call",
            f"unsupported tool: {name}",
            expected={"field": "name", "type": "supported_tool_name"},
            found={"field": "name", "value": name},
        )
    arguments = _coerce_json_object(function.get("arguments", {}))
    normalized_name = name.lower()
    if "type" not in arguments and "_metadata" not in arguments:
        if normalized_name in {"do", "phone_do"}:
            arguments = {"type": "do", **arguments}
        elif normalized_name in {"finish", "phone_finish"}:
            arguments = {"type": "finish", **arguments}
    return adapt_json_action(arguments)


def _coerce_json_object(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ActionAdapterError("invalid_json", "payload is not valid JSON") from exc
    else:
        data = payload
    if not isinstance(data, dict):
        raise ActionAdapterError(
            "invalid_json",
            "payload must be a JSON object",
            expected={"type": "object"},
            found={"type": type(data).__name__},
        )
    return data


def _canonical_action_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ActionAdapterError(
            "missing_field",
            "do.action must be a string",
            expected={"field": "action", "type": "string"},
            found={"field": "action", "value": value},
        )
    if value in CANONICAL_ACTIONS:
        return value
    canonical = ACTION_ALIASES.get(value.strip().lower())
    if canonical is None:
        raise ActionAdapterError(
            "unknown_action",
            f"unknown action: {value}",
            expected={"field": "action", "type": "canonical_action"},
            found={"action": value},
        )
    return canonical


def _reject_dangerous_provider_fields(data: dict[str, Any]) -> None:
    dangerous = {key for key in data if key.lower() in DANGEROUS_PROVIDER_FIELDS}
    if dangerous:
        raise ActionAdapterError(
            "unsafe_value",
            f"dangerous fields are not allowed: {sorted(dangerous)}",
            expected={"field": "provider_fields", "type": "safe_fields"},
            found={"field": sorted(dangerous)},
        )


def _reject_unexpected_provider_fields(data: dict[str, Any], allowed: set[str]) -> None:
    extras = set(data) - allowed
    if extras:
        raise ActionAdapterError(
            "unsafe_value",
            f"unsupported provider fields: {sorted(extras)}",
            expected={"field": sorted(allowed), "type": "allowed_fields"},
            found={"field": sorted(extras)},
        )


def _extract_point(data: dict[str, Any]) -> list[int | float]:
    if "element" in data:
        return _validate_point(data["element"], "element")
    if "x" in data and "y" in data:
        return _validate_point([data["x"], data["y"]], "x/y")
    raise ActionAdapterError(
        "missing_field",
        "point action requires element or x/y",
        expected={"field": "element", "type": "[x, y]"},
        found={"field": "element", "value": None},
    )


def _validate_point(value: Any, field_name: str) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ActionAdapterError(
            "unsafe_value",
            f"{field_name} must be [x, y]",
            expected={"field": field_name, "type": "[x, y]"},
            found={"field": field_name, "value": value},
        )
    point = []
    for index, coordinate in enumerate(value):
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            raise ActionAdapterError(
                "unsafe_value",
                "coordinates must be numeric",
                expected={"field": f"{field_name}[{index}]", "type": "number"},
                found={"field": f"{field_name}[{index}]", "value": coordinate},
            )
        if coordinate < 0 or coordinate > 1000:
            raise ActionAdapterError(
                "unsafe_value",
                "coordinates must be in 0-1000",
                expected={"field": f"{field_name}[{index}]", "range": "0..1000"},
                found={"field": f"{field_name}[{index}]", "value": coordinate},
            )
        point.append(coordinate)
    return point


def _has_object_selector(intent: dict[str, Any]) -> bool:
    if isinstance(intent.get("target_object_id"), str) and intent["target_object_id"].strip():
        return True
    if isinstance(intent.get("object_filter"), dict):
        return True
    if isinstance(intent.get("object_role"), str) and isinstance(intent.get("ordinal"), int):
        return True
    return False
