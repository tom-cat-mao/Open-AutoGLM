"""Execute node: execute action → strip images → append assistant message."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.handler import ActionResult, finish
from phone_agent.graph.context import (
    build_action_outcome_summary,
    context_enabled,
    get_context_mode,
)
from phone_agent.graph.tools import dispatch_tool
from phone_agent.graph.trace import emit_trace
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def _strip_and_append(
    messages: list[dict], thinking: str, action_raw: str
) -> list[dict]:
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

    Uses dispatch_tool for action execution.

    Corresponds to agent.py:185-243 (execute + strip images + append context + finish check).
    """
    configurable = config.get("configurable", {})
    verbose = configurable.get("verbose", True)
    device_factory = configurable.get("device_factory")

    action_parsed = state.get("action_parsed")
    messages = list(state["messages"])  # copy
    thinking = state.get("thinking", "")
    action_raw = state.get("action_raw", "")
    screen_width = state["screen_width"]
    screen_height = state["screen_height"]
    device_id = state.get("device_id")
    context_mode = get_context_mode(state, config)

    def _context_update(result_dict: dict) -> dict:
        if not context_enabled(context_mode):
            return {"context_mode": context_mode}
        outcome_state = {
            **state,
            "action_result": result_dict,
            "current_app": state.get("current_app") or "unknown",
            "context_mode": context_mode,
        }
        return {
            "context_mode": context_mode,
            "action_outcome_summary": build_action_outcome_summary(outcome_state),
        }

    # Plan-stage parse/model failures are terminal and must not be converted into
    # a successful finish or a generic execute error.
    if state.get("finished") and state.get("error"):
        result_dict = state.get("action_result") or {
            "success": False,
            "should_finish": True,
            "message": state.get("error"),
        }
        emit_trace(
            config,
            state,
            "execute",
            "execute_error",
            {"message": state.get("error"), "failure_cause": state.get("failure_cause")},
        )
        return {
            "action_result": result_dict,
            "finished": True,
            "error": state.get("error"),
            "failure_cause": state.get("failure_cause"),
            **_context_update(result_dict),
        }

    # 1. Check action_parsed
    if action_parsed is None:
        emit_trace(config, state, "execute", "execute_error", {"message": "No action to execute"})
        return {
            "action_result": ActionResult(
                success=False, should_finish=True, message="No action to execute"
            ).__dict__,
            "finished": True,
            "error": "No action to execute",
            **_context_update({"success": False, "should_finish": True, "message": "No action to execute"}),
        }

    if action_parsed.get("_metadata") == "finish":
        result = ActionResult(
            success=True,
            should_finish=True,
            message=action_parsed.get("message"),
        )
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(config, state, "execute", "execute_finish", {"message": result.message})
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            **_context_update(result.__dict__),
        }

    if action_parsed.get("_metadata") != "do":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Unknown action type: {action_parsed.get('_metadata')}",
        )
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(config, state, "execute", "execute_error", {"message": result.message})
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
            **_context_update(result.__dict__),
        }

    # 2. Pending execute branch (BUG 2 fix)
    # If confirm was accepted, execute the pending action directly
    if state.get("pending_execute"):
        if state.get("interrupt_result") is not True:
            result = ActionResult(
                success=False,
                should_finish=True,
                message="Pending sensitive action requires accepted confirmation",
            )
            emit_trace(
                config,
                state,
                "execute",
                "execute_error",
                {"message": result.message, "pending_execute": True},
            )
            return {
                "action_result": result.__dict__,
                "messages": messages,
                "finished": True,
                "error": result.message,
                "pending_execute": False,
                "action_confirmed": False,
                **_context_update(result.__dict__),
            }
        # CRITICAL-1: do NOT call _strip_and_append again (images already stripped on first pass)
        try:
            result = dispatch_tool(
                action_parsed,
                screen_width,
                screen_height,
                device_id,
                device_factory=device_factory,
            )
        except Exception as e:
            if verbose:
                traceback.print_exc()
            result = ActionResult(
                success=False, should_finish=True, message=f"Action failed: {e}"
            )
        emit_trace(
            config,
            state,
            "execute",
            "execute_result",
            {"action": action_parsed.get("action"), "result": result.__dict__, "pending_execute": True},
        )

        # CRITICAL-2: mark action_confirmed=True (keep action_parsed for reflect)
        finished = result.should_finish
        return {
            "action_result": result.__dict__,
            "messages": messages,  # unchanged (already stripped + assistant appended)
            "finished": finished,
            "pending_execute": False,
            "action_confirmed": True,
            "pending_interrupt": None,
            "interrupt_result": None,
            **_context_update(result.__dict__),
        }

    # 3. Human-in-the-Loop checks (Phase 2)
    action_name = action_parsed.get("action")
    if action_name == "Take_over":
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(
            config,
            state,
            "execute",
            "takeover_interrupt",
            {"interrupt_message": action_parsed.get("message", "User intervention required")},
        )
        return {
            "messages": messages,
            "pending_interrupt": "takeover",
            "interrupt_message": action_parsed.get(
                "message", "User intervention required"
            ),
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
        }

    if action_name == "Tap" and "message" in action_parsed:
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(
            config,
            state,
            "execute",
            "confirm_interrupt",
            {"interrupt_message": action_parsed["message"]},
        )
        return {
            "messages": messages,
            "pending_interrupt": "confirmation",
            "interrupt_message": action_parsed["message"],
            "pending_execute": True,
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
        }

    # 4. Execute action via tool dispatch
    try:
        result = dispatch_tool(
            action_parsed,
            screen_width,
            screen_height,
            device_id,
            device_factory=device_factory,
        )
    except Exception as e:
        if verbose:
            traceback.print_exc()
        result = ActionResult(
            success=False, should_finish=True, message=f"Action failed: {e}"
        )
    emit_trace(
        config,
        state,
        "execute",
        "execute_result",
        {"action": action_parsed.get("action"), "result": result.__dict__},
    )

    # 5. Strip images and append assistant message
    messages = _strip_and_append(messages, thinking, action_raw)

    # 6. Check should_finish
    finished = result.should_finish

    return {
        "action_result": result.__dict__,
        "messages": messages,
        "finished": finished,
        **_context_update(result.__dict__),
    }
