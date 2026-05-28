"""Execute node: execute action → strip images → append assistant message."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.handler import ActionResult, finish
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def execute_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Execute node: run action, strip images, append assistant message.

    Corresponds to agent.py:185-243 (execute + strip images + append context + finish check).
    """
    configurable = config.get("configurable", {})
    action_handler = configurable["action_handler"]
    verbose = configurable.get("verbose", True)

    action_parsed = state.get("action_parsed")
    messages = list(state["messages"])  # copy
    thinking = state.get("thinking", "")
    action_raw = state.get("action_raw", "")
    screen_width = state["screen_width"]
    screen_height = state["screen_height"]

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
        # Strip images from last user message
        if messages:
            messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
        # Append assistant message
        messages.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action_raw}</answer>"
            )
        )
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
        if messages:
            messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
        messages.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action_raw}</answer>"
            )
        )
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
        }

    # 2. Human-in-the-Loop checks (Phase 2)
    action_name = action_parsed.get("action")
    if action_name == "Take_over":
        # Route to takeover node instead of executing
        if messages:
            messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
        messages.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action_raw}</answer>"
            )
        )
        return {
            "messages": messages,
            "pending_interrupt": "takeover",
            "interrupt_message": action_parsed.get("message", "User intervention required"),
        }

    if action_name == "Tap" and "message" in action_parsed:
        # Route to confirm node instead of executing
        if messages:
            messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])
        messages.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action_raw}</answer>"
            )
        )
        return {
            "messages": messages,
            "pending_interrupt": "confirmation",
            "interrupt_message": action_parsed["message"],
        }

    # 3. Execute action
    try:
        result = action_handler.execute(action_parsed, screen_width, screen_height)
    except Exception as e:
        if verbose:
            traceback.print_exc()
        try:
            result = action_handler.execute(
                finish(message=str(e)), screen_width, screen_height
            )
        except Exception:
            result = ActionResult(
                success=False, should_finish=True, message=f"Action failed: {e}"
            )

    # 4. Strip images from last user message
    if messages:
        messages[-1] = MessageBuilder.remove_images_from_message(messages[-1])

    # 5. Append assistant message
    messages.append(
        MessageBuilder.create_assistant_message(
            f"<think>{thinking}</think><answer>{action_raw}</answer>"
        )
    )

    # 6. Check should_finish
    finished = result.should_finish

    return {
        "action_result": result.__dict__,
        "messages": messages,
        "finished": finished,
    }
