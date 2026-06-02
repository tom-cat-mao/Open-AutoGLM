"""Action handling module for Phone Agent."""

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action, adapt_tool_calls
from phone_agent.actions.handler import ActionResult, parse_action, do, finish

__all__ = [
    "ActionAdapterError",
    "ActionResult",
    "adapt_json_action",
    "adapt_tool_calls",
    "parse_action",
    "do",
    "finish",
]
