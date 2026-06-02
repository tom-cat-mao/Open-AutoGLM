"""Provider action format adapters.

Adapters convert provider-facing JSON/tool_calls into the internal canonical
action dictionary consumed by the existing graph execution path. They never
execute tools and fail closed on unknown or unsafe input.
"""

from __future__ import annotations

import json
from typing import Any


class ActionAdapterError(ValueError):
    """Adapter error with a stable machine-readable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    "take_over": "Take_over",
}
CANONICAL_ACTIONS = set(ACTION_ALIASES.values())
TOOL_NAME_ALIASES = {"do", "finish", "phone_do", "phone_finish"}


def adapt_json_action(payload: str | dict[str, Any]) -> dict[str, Any]:
    """Adapt provider-facing JSON into an internal canonical action dict."""
    data = _coerce_json_object(payload)
    action_type = data.get("type") or data.get("_metadata")
    if action_type == "finish":
        message = data.get("message")
        if not isinstance(message, str):
            raise ActionAdapterError("missing_field", "finish.message must be a string")
        return {"_metadata": "finish", "message": message}
    if action_type != "do":
        raise ActionAdapterError("unknown_action", "action type must be do or finish")

    action_name = _canonical_action_name(data.get("action"))
    action: dict[str, Any] = {"_metadata": "do", "action": action_name}
    if "message" in data:
        if not isinstance(data["message"], str):
            raise ActionAdapterError("unsafe_value", "message must be a string")
        action["message"] = data["message"]

    if action_name in {"Tap", "Double Tap", "Long Press"}:
        action["element"] = _extract_point(data)
    elif action_name == "Swipe":
        start = data.get("start") or data.get("start_element")
        end = data.get("end") or data.get("end_element")
        if start is None or end is None:
            raise ActionAdapterError("missing_field", "swipe requires start and end")
        action["start"] = _validate_point(start, "start")
        action["end"] = _validate_point(end, "end")
    elif action_name in {"Type", "Type_Name", "Note", "Call_API", "Interact", "Take_over"}:
        text_value = data.get("text", data.get("message"))
        if not isinstance(text_value, str):
            raise ActionAdapterError("missing_field", f"{action_name} requires text/message")
        if action_name in {"Take_over", "Note", "Call_API", "Interact"}:
            action["message"] = text_value
        else:
            action["text"] = text_value
    elif action_name == "Launch":
        app = data.get("app") or data.get("app_name")
        if not isinstance(app, str):
            raise ActionAdapterError("missing_field", "Launch requires app")
        action["app"] = app
    elif action_name == "Wait":
        duration = data.get("duration", "1 seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            duration = f"{duration} seconds"
        if not isinstance(duration, str):
            raise ActionAdapterError("unsafe_value", "Wait duration must be string or number")
        action["duration"] = duration
    elif action_name not in {"Back", "Home"}:
        raise ActionAdapterError("unknown_action", f"unsupported action: {action_name}")
    return action


def adapt_tool_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Adapt an already aggregated OpenAI tool_calls list into one action dict."""
    if len(tool_calls) != 1:
        raise ActionAdapterError("unsupported_tool_call", "exactly one tool call is supported")
    tool_call = tool_calls[0]
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise ActionAdapterError("unsupported_tool_call", "tool call missing function")
    name = function.get("name")
    if not isinstance(name, str) or name.lower() not in TOOL_NAME_ALIASES:
        raise ActionAdapterError("unsupported_tool_call", f"unsupported tool: {name}")
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
        raise ActionAdapterError("invalid_json", "payload must be a JSON object")
    return data


def _canonical_action_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ActionAdapterError("missing_field", "do.action must be a string")
    if value in CANONICAL_ACTIONS:
        return value
    canonical = ACTION_ALIASES.get(value.strip().lower())
    if canonical is None:
        raise ActionAdapterError("unknown_action", f"unknown action: {value}")
    return canonical


def _extract_point(data: dict[str, Any]) -> list[int | float]:
    if "element" in data:
        return _validate_point(data["element"], "element")
    if "x" in data and "y" in data:
        return _validate_point([data["x"], data["y"]], "x/y")
    raise ActionAdapterError("missing_field", "point action requires element or x/y")


def _validate_point(value: Any, field_name: str) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ActionAdapterError("unsafe_value", f"{field_name} must be [x, y]")
    point = []
    for coordinate in value:
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            raise ActionAdapterError("unsafe_value", "coordinates must be numeric")
        if coordinate < 0 or coordinate > 1000:
            raise ActionAdapterError("unsafe_value", "coordinates must be in 0-1000")
        point.append(coordinate)
    return point
