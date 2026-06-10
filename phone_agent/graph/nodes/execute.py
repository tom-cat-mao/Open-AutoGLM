"""Execute node: execute action → strip images → append assistant message."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.handler import ActionResult, finish
from phone_agent.actions.gesture import compile_action_to_gesture
from phone_agent.actions.safety import decide_safety
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.graph.context import (
    build_action_outcome_summary,
    context_enabled,
    get_context_mode,
    sanitize_context_payload,
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


def _clear_stale_reflection_for_skip_action(action_name: str | None) -> dict:
    """Clear reflection advice after actions that replan without reflect."""
    if action_name not in {"Wait", "Note", "Call_API", "Interact"}:
        return {}
    return {
        "reflection": None,
        "action_succeeded": True,
        "reflection_verdict": None,
        "failure_cause": None,
        "suggested_strategy": None,
    }


def _layered_error(layer: str, code: str, *, recoverable: bool = False, retry_policy: str = "none") -> dict:
    """Build stable layered error fields for terminal execute failures."""

    return {
        "error_layer": layer,
        "error_code": code,
        "recoverable": recoverable,
        "retry_policy": retry_policy,
    }


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

    def _context_update(result_dict: dict, state_overrides: dict | None = None) -> dict:
        if not context_enabled(context_mode):
            return {"context_mode": context_mode}
        outcome_state = {
            **state,
            **(state_overrides or {}),
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
            "error_layer": state.get("error_layer"),
            "error_code": state.get("error_code"),
            "recoverable": state.get("recoverable"),
            "retry_policy": state.get("retry_policy"),
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
            **_layered_error("execution", "missing_action"),
            **_context_update({"success": False, "should_finish": True, "message": "No action to execute"}),
        }

    try:
        action_parsed = validate_action(action_parsed)
    except ActionValidationError as exc:
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Invalid action: {exc.code}: {exc}",
        )
        emit_trace(
            config,
            state,
            "execute",
            "execute_error",
            {"message": result.message, "failure_cause": "action_validation_failed", "validation_error_code": exc.code},
        )
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "action_validation_failed",
            **_layered_error("validation", exc.code),
            **_context_update(result.__dict__),
        }

    pending_execute_confirmed = state.get("pending_execute") and state.get("interrupt_result") is True
    safety_decision = decide_safety(action_parsed)
    safety_route = "approved" if pending_execute_confirmed else safety_decision.route
    safety_reason = "confirmation_accepted" if pending_execute_confirmed else safety_decision.reason
    emit_trace(
        config,
        state,
        "execute",
        "safety_decision",
        {
            "route": safety_route,
            "interrupt_type": safety_decision.interrupt_type,
            "reason": safety_reason,
            "confirmation_accepted": pending_execute_confirmed,
            "decision": safety_decision.sanitized_trace_payload or {},
        },
    )
    if safety_route == "rejected":
        result = ActionResult(
            success=False,
            should_finish=True,
            message=f"Action rejected by safety gate: {safety_decision.reason}",
        )
        emit_trace(config, state, "execute", "execute_error", {"message": result.message})
        return {
            "action_result": result.__dict__,
            "messages": messages,
            "finished": True,
            "error": result.message,
            "failure_cause": "action_safety_rejected",
            **_layered_error("safety", "action_safety_rejected"),
            **_context_update(result.__dict__),
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
            "failure_cause": "unknown_action_type",
            **_layered_error("validation", "unknown_action_type"),
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
                "failure_cause": "confirmation_required",
                **_layered_error("safety", "confirmation_required", recoverable=True, retry_policy="takeover"),
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
            execution_error = _layered_error("execution", "dispatch_failed")
            result = ActionResult(
                success=False, should_finish=True, message=f"Action failed: {e}"
            )
        else:
            execution_error = {}
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
            **({"error": result.message, "failure_cause": "execution_failed", **execution_error} if execution_error else {}),
            **_context_update(result.__dict__),
        }

    # 3. Human-in-the-Loop checks (Phase 2)
    action_name = action_parsed.get("action")
    if safety_decision.route == "takeover":
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(
            config,
            state,
            "execute",
            "takeover_interrupt",
            {
                "interrupt_message": action_parsed.get("message", "User intervention required"),
                "safety_reason": safety_decision.reason,
            },
        )
        return {
            "messages": messages,
            "pending_interrupt": safety_decision.interrupt_type or "takeover",
            "interrupt_message": action_parsed.get(
                "message", "User intervention required"
            ),
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
        }

    if safety_decision.route == "confirm":
        messages = _strip_and_append(messages, thinking, action_raw)
        emit_trace(
            config,
            state,
            "execute",
            "confirm_interrupt",
            {"interrupt_message": action_parsed["message"], "safety_reason": safety_decision.reason},
        )
        return {
            "messages": messages,
            "pending_interrupt": safety_decision.interrupt_type or "confirmation",
            "interrupt_message": action_parsed["message"],
            "pending_execute": True,
            "hitl_count": state.get("hitl_count", 0) + 1,
            "context_mode": context_mode,
        }

    # 4. Execute action via tool dispatch
    gesture_trace = None
    try:
        gesture_trace = compile_action_to_gesture(action_parsed).to_dict()
    except Exception:
        gesture_trace = None
    emit_trace(
        config,
        state,
        "execute",
        "gesture_compiled",
        {"gesture": sanitize_context_payload(gesture_trace, consumer="trace_payload"), "coordinate_space": "relative_0_1000"},
    )
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
        execution_error = _layered_error("execution", "dispatch_failed")
        result = ActionResult(
            success=False, should_finish=True, message=f"Action failed: {e}"
        )
    else:
        execution_error = {}
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
    skip_reflection_updates = _clear_stale_reflection_for_skip_action(action_name)

    return {
        "action_result": result.__dict__,
        "messages": messages,
        "finished": finished,
        **({"error": result.message, "failure_cause": "execution_failed", **execution_error} if execution_error else {}),
        **skip_reflection_updates,
        **_context_update(result.__dict__, skip_reflection_updates),
    }
