"""Action handling module for Phone Agent."""

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action, adapt_tool_calls
from phone_agent.actions.capability import ToolCapability, get_all_capabilities, get_tool_capability
from phone_agent.actions.receipt import ActionReceipt
from phone_agent.actions.result import ActionResult

__all__ = [
    "ActionAdapterError",
    "ActionReceipt",
    "ActionResult",
    "ToolCapability",
    "adapt_json_action",
    "adapt_tool_calls",
    "get_all_capabilities",
    "get_tool_capability",
]
