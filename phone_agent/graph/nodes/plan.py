"""Plan node: screenshot → build messages → model inference → parse action."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.handler import parse_action
from phone_agent.actions.repair import ActionRepairError, repair_action
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.config import get_prompt_version, get_system_prompt
from phone_agent.graph.context import (
    build_context_metrics,
    compact_messages_for_request,
    get_context_mode,
    sanitize_context_payload,
    select_plan_context,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def _build_reflection_context(state: "AgentState") -> str:
    reflection = state.get("reflection")
    verdict = state.get("reflection_verdict")
    cause = state.get("failure_cause")
    strategy = state.get("suggested_strategy")
    parts = []
    if reflection:
        safe_reflection = sanitize_context_payload(reflection, "reflection")
        parts.append(f"** Reflection **\n\n{safe_reflection}")
    structured = []
    if verdict:
        structured.append(f"verdict: {verdict}")
    if cause:
        structured.append(f"failure_cause: {cause}")
    if strategy:
        structured.append(f"suggested_strategy: {strategy}")
    if structured:
        parts.append("** Structured Reflection **\n\n" + "\n".join(structured))
    return "\n\n".join(parts)


def _validate_with_limited_repair(
    action: dict,
    *,
    raw_action: str,
    parse_metadata: dict,
) -> tuple[dict | None, str | None, dict]:
    """Validate adapter/parser output, with one narrow repair attempt before fail-closed."""

    try:
        validated = validate_action(action)
        return validated, None, {**parse_metadata, "validation_success": True}
    except ActionValidationError as validation_exc:
        repair_metadata = {
            **parse_metadata,
            "validation_success": False,
            "validation_error_code": validation_exc.code,
            "repair_attempted": True,
        }
        try:
            repaired = repair_action(
                action,
                error_code=validation_exc.code,
                raw_summary=f"len={len(raw_action)}",
            )
            validated = validate_action(repaired)
        except ActionRepairError as repair_exc:
            error = f"Model parse failed: validation: {validation_exc.code}: {validation_exc}"
            return (
                None,
                error,
                {
                    **repair_metadata,
                    "repair_success": False,
                    "repair_error_code": repair_exc.code,
                    "parse_success": False,
                    "parse_error_code": validation_exc.code,
                },
            )
        except ActionValidationError as second_validation_exc:
            error = (
                "Model parse failed: validation after repair: "
                f"{second_validation_exc.code}: {second_validation_exc}"
            )
            return (
                None,
                error,
                {
                    **repair_metadata,
                    "repair_success": True,
                    "second_validation_success": False,
                    "second_validation_error_code": second_validation_exc.code,
                    "parse_success": False,
                    "parse_error_code": second_validation_exc.code,
                },
            )
        return (
            validated,
            None,
            {
                **repair_metadata,
                "repair_success": True,
                "second_validation_success": True,
                "validation_success": True,
            },
        )


def plan_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Plan node: capture screen, build messages, get model response, parse action.

    Corresponds to agent.py:148-183 (capture + build messages + inference + parse).
    """
    # Get dependencies from config
    configurable = config.get("configurable", {})
    model_client = configurable["model_client"]
    device_factory = configurable["device_factory"]
    device_id = state.get("device_id")
    lang = state.get("lang", "cn")

    step_count = state["step_count"]
    task = state["task"]
    messages = list(state["messages"])  # copy

    # 1. Capture screen
    screenshot = device_factory.get_screenshot(device_id)
    current_app = device_factory.get_current_app(device_id)
    context_mode = get_context_mode(state, config)
    prompt_version = get_prompt_version(configurable.get("prompt_version"))
    context_selection = select_plan_context(
        state,
        mode=context_mode,
        lang=lang,
        prompt_version=prompt_version,
    )
    context_block = context_selection.context_block
    context_metrics = context_selection.metrics()
    emit_trace(
        config,
        state,
        "plan",
        "plan_start",
        {"task": task, "current_app": current_app, **context_metrics},
    )

    # 2. Build new messages (only the new ones, reducer will append)
    new_messages = []
    if step_count == 0:
        system_prompt = configurable.get("system_prompt") or get_system_prompt(
            lang,
            configurable.get("output_mode", "text_dsl"),
            prompt_version=prompt_version,
        )
        new_messages.append(MessageBuilder.create_system_message(system_prompt))

        screen_info = MessageBuilder.build_screen_info(current_app)
        text_content = f"{task}\n\n{screen_info}"
        if context_block:
            text_content = f"{text_content}\n\n{context_block}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content, image_base64=screenshot.base64_data
            )
        )
    else:
        screen_info = MessageBuilder.build_screen_info(current_app)
        reflection_context = _build_reflection_context(state)
        if reflection_context:
            text_content = f"** Screen Info **\n\n{screen_info}\n\n{reflection_context}"
        else:
            text_content = f"** Screen Info **\n\n{screen_info}"
        if context_block:
            text_content = f"{text_content}\n\n{context_block}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content, image_base64=screenshot.base64_data
            )
        )

    # 3. Model inference (pass full messages for context)
    full_messages = list(state["messages"]) + new_messages
    request_messages, context_selection = compact_messages_for_request(
        full_messages, context_selection
    )
    context_metrics = context_selection.metrics()
    try:
        response = model_client.request(request_messages)
    except Exception as e:
        if configurable.get("verbose", True):
            traceback.print_exc()
        error_message = f"Model error: {e}"
        parse_metadata = getattr(e, "parse_metadata", {}) or {}
        emit_trace(
            config,
            state,
            "plan",
            "plan_error",
            {
                "message": str(e),
                "failure_cause": "model_parse_failed",
                "parse_metadata": parse_metadata,
                "parse_error_code": parse_metadata.get("parse_error_code"),
                **context_metrics,
            },
        )
        return {
            "messages": new_messages,
            "step_count": step_count + 1,
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "thinking": "",
            "action_raw": "",
            "action_parsed": None,
            "action_result": {
                "success": False,
                "should_finish": True,
                "message": error_message,
            },
            "error": error_message,
            "failure_cause": "model_parse_failed",
            "parse_metadata": parse_metadata,
            "finished": True,
            "action_confirmed": False,
            "context_mode": context_mode,
            **context_metrics,
        }

    # 4. Parse action, then validate canonical IR before safety/execution.
    parse_error = None
    parse_metadata = getattr(response, "parse_metadata", {}) or {}
    try:
        stripped_action = response.action.strip()
        if stripped_action.startswith("{"):
            adapter_used = parse_metadata.get("adapter_used")
            configured_output_mode = configurable.get("output_mode", "text_dsl")
            if configured_output_mode == "text_dsl" and adapter_used not in {
                "json_schema",
                "tool_calls",
            }:
                raise ActionAdapterError(
                    "invalid_json", "JSON action response is not enabled in text_dsl mode"
                )
            action_parsed = adapt_json_action(stripped_action)
        else:
            action_parsed = parse_action(response.action)
    except ActionAdapterError as exc:
        action_parsed = None
        parse_error = f"Model parse failed: {exc.code}: {exc}"
        parse_metadata = {
            **parse_metadata,
            "parse_success": False,
            "parse_error_code": exc.code,
        }
    except ValueError as exc:
        action_parsed = None
        parse_error = f"Model parse failed: {exc}"
        parse_metadata = {
            **parse_metadata,
            "parse_success": False,
            "parse_error_code": "parse_error",
        }

    if action_parsed is not None:
        action_parsed, parse_error, parse_metadata = _validate_with_limited_repair(
            action_parsed,
            raw_action=response.action,
            parse_metadata=parse_metadata,
        )

    emit_trace(
        config,
        state,
        "plan",
        "plan_result",
        {
            "current_app": current_app,
            "thinking": response.thinking,
            "action_raw": response.action,
            "action": action_parsed.get("action") if isinstance(action_parsed, dict) else None,
            "metadata": action_parsed.get("_metadata") if isinstance(action_parsed, dict) else None,
            "parse_success": parse_error is None,
            "parse_error": parse_error,
            "failure_cause": "model_parse_failed" if parse_error else None,
            "parse_metadata": parse_metadata,
            "validation_success": parse_metadata.get("validation_success"),
            "validation_error_code": parse_metadata.get("validation_error_code"),
            "repair_attempted": parse_metadata.get("repair_attempted", False),
            "repair_success": parse_metadata.get("repair_success"),
            "repair_error_code": parse_metadata.get("repair_error_code"),
            **context_metrics,
        },
    )

    if parse_error:
        return {
            "messages": new_messages,
            "step_count": step_count + 1,
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "thinking": response.thinking,
            "action_raw": response.action,
            "action_parsed": None,
            "action_result": {
                "success": False,
                "should_finish": True,
                "message": parse_error,
            },
            "error": parse_error,
            "failure_cause": "model_parse_failed",
            "parse_metadata": parse_metadata,
            "finished": True,
            "action_confirmed": False,
            "context_mode": context_mode,
            **context_metrics,
        }

    return {
        "messages": new_messages,
        "step_count": step_count + 1,
        "screenshot_b64": screenshot.base64_data,
        "current_app": current_app,
        "screen_width": screenshot.width,
        "screen_height": screenshot.height,
        "thinking": response.thinking,
        "action_raw": response.action,
        "action_parsed": action_parsed,
        "action_confirmed": False,
        "context_mode": context_mode,
        **context_metrics,
    }
