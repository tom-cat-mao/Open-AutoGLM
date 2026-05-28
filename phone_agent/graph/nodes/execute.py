"""Execute node: execute action → strip images → append assistant message."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.handler import ActionResult, finish
from phone_agent.graph.tools import dispatch_tool
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def _strip_and_append(messages: list[dict], thinking: str, action_raw: str) -> list[dict]:
    """Strip images from last user message and append assistant message."""
    if messages:
        messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
    messages.append(
        MessageBuilder.create_assistant_message(
            f"<think...>{thinking}</think...>\n<answer>{action_raw}</answer>"
        )
    )
    return messages


def execute_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Execute node: run action, strip images, append assistant message.

    Uses dispatch_tool (Phase 3) for action execution, falling back to
    action_handler.execute() if use_tools is False in config.

    Corresponds to agent.py:185-243 (execute + strip images + append context + finish check).
    """
    configurable = config.get("configurable", {})
    verbose = configurable.get("verbose", True)
    use_tools = configurable.get("use_tools", True)  # Phase 3: default to tool dispatch

    action_parsed = state.get("action_parsed")
    messages = list(state["messages"])  # copy
    thinking = state.get("thinking", "")
    action_raw = state.get("action_raw", "")
    screen_width = state["screen_width"]
    screen_height = state["screen_height"]
    device_id = state.get("device_id")

    # 1. Check action_parsed
    if action_parsed is None:
        return {
            "action_result": ActionResult(
                success=False, should_finish=True, message="No action to execute"
            ).__dict__,
            "finished": True,
            "error": "No action to execute",
        }

    if action_parsed.get("_metadata") == "finish":
        result = ActionResult(
            success=True,
            should_finish=True,
            message=action_parsed.get("message"),
        )
        messages = _strip_and_append(messages, thinking, action_raw)
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
        }

    if action_parsed.get("_metadata") != "do":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Unknown action type: {action_parsed.get('_metadata')}",
        )
        messages = _strip_and_append(messages, thinking, action_raw)
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
        }

    # 2. Human-in-the-Loop checks (Phase 2)
    action_name = action_parsed.get("action")
    if action_name == "Take_over":
        messages = _strip_and_append(messages, thinking, action_raw)
        return {
            "messages": messages,
            "pending_interrupt": "takeover",
            "interrupt_message": action_parsed.get("message", "User intervention required"),
        }

    if action_name == "Tap" and "message" in action_parsed:
        messages = _strip_and_append(messages, thinking, action_raw)
        return {
            "messages": messages,
            "pending_interrupt": "confirmation",
            "interrupt_message": action_parsed["message"],
        }

    # 3. Execute action via tool dispatch (Phase 3) or legacy ActionHandler
    try:
        if use_tools:
            result = dispatch_tool(action_parsed, screen_width, screen_height, device_id)
        else:
            action_handler = configurable["action_handler"]
            result = action_handler.execute(action_parsed, screen_width, screen_height)
    except Exception as e:
        if verbose:
            traceback.print_exc()
        result = ActionResult(
            success=False, should_finish=True, message=f"Action failed: {e}"
        )

    # 4. Strip images and append assistant message
    messages = _strip_and_append(messages, thinking, action_raw)

    # 5. Check should_finish
    finished = result.should_finish

    return {
        "action_result": result.__dict__,
        "messages": messages,
        "finished": finished,
    }
