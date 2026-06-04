"""Plan node: screenshot → build messages → model inference → parse action."""

from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.actions.handler import parse_action
from phone_agent.actions.ir import is_intent_dict
from phone_agent.actions.repair import ActionRepairError, repair_action
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.config import get_prompt_version, get_system_prompt
from phone_agent.config.apps import get_app_registry_summary
from phone_agent.graph.context import (
    build_context_metrics,
    compact_messages_for_request,
    get_context_mode,
    sanitize_context_payload,
    select_plan_context,
)
from phone_agent.graph.observation import build_observation
from phone_agent.graph.trace import emit_trace
from phone_agent.model.client import MessageBuilder
from phone_agent.model.client import ModelParseError

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


def _safe_request(model_client, messages: list[dict], *, output_mode: str | None = None):
    """Call model_client.request with backward-compatible kwargs."""

    if output_mode is None:
        return model_client.request(messages)
    try:
        return model_client.request(messages, output_mode=output_mode)
    except TypeError as type_error:
        if "output_mode" not in str(type_error):
            raise
        return model_client.request(messages)


def _build_parse_retry_messages(messages: list[dict], parse_error: str) -> list[dict]:
    """Append a trace-safe format-only retry instruction."""

    retry_text = (
        "Previous response failed format/schema parsing. Retry once and only fix the "
        "output format. Do not invent coordinates, marks, private text, or new action semantics. "
        f"Error class: {parse_error.split(':', 1)[0]}"
    )
    return list(messages) + [MessageBuilder.create_user_message(text=retry_text)]


def _parse_and_ground_response(response, configurable: dict, mark_registry, screen_id: str | None):
    """Parse provider response, optionally ground IntentIR, then validate canonical ActionIR."""

    parse_error = None
    parse_metadata = getattr(response, "parse_metadata", {}) or {}
    intent_raw = None
    grounding_error = None
    structured_json_response = False
    try:
        stripped_action = response.action.strip()
        configured_output_mode = configurable.get("output_mode", "text_dsl")
        structured_json_response = stripped_action.startswith("{") and configured_output_mode != "text_dsl"
        if stripped_action.startswith("{"):
            adapter_used = parse_metadata.get("adapter_used")
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

    if action_parsed is not None and is_intent_dict(action_parsed):
        intent_raw = dict(action_parsed)
        try:
            action_parsed = ground_intent_to_action(
                action_parsed,
                mark_registry=mark_registry,
                screen_id=screen_id,
            )
            parse_metadata = {
                **parse_metadata,
                "intent_detected": True,
                "grounding_success": True,
                "target_mark_id": intent_raw.get("target_mark_id"),
            }
        except GroundingError as exc:
            action_parsed = None
            grounding_error = exc.code
            parse_error = f"Model parse failed: grounding: {exc.code}: {exc}"
            parse_metadata = {
                **parse_metadata,
                "intent_detected": True,
                "grounding_success": False,
                "grounding_error_code": exc.code,
                "parse_success": False,
                "parse_error_code": exc.code,
            }

    if (
        action_parsed is not None
        and intent_raw is None
        and (
            parse_metadata.get("adapter_used") in {"json_schema", "tool_calls"}
            or configurable.get("output_mode") in {"json_schema", "tool_calls"}
            or structured_json_response
        )
        and isinstance(action_parsed, dict)
        and action_parsed.get("action") in {"Tap", "Double Tap", "Long Press"}
    ):
        action_parsed = None
        grounding_error = "mark_required"
        parse_error = "Model parse failed: grounding: mark_required: structured screen targeting requires target_mark_id"
        parse_metadata = {
            **parse_metadata,
            "grounding_success": False,
            "grounding_error_code": "mark_required",
            "parse_success": False,
            "parse_error_code": "mark_required",
        }

    if action_parsed is not None:
        action_parsed, parse_error, parse_metadata = _validate_with_limited_repair(
            action_parsed,
            raw_action=response.action,
            parse_metadata=parse_metadata,
        )
    return action_parsed, parse_error, parse_metadata, intent_raw, grounding_error


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
    screen_marks = configurable.get("screen_marks")
    if screen_marks is None and hasattr(device_factory, "get_screen_marks"):
        screen_marks = device_factory.get_screen_marks(device_id)
    observation = build_observation(
        screenshot=screenshot,
        current_app=current_app,
        marks=screen_marks,
    )
    mark_registry = observation.mark_registry
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
        custom_prompt = configurable.get("system_prompt")
        system_prompt = custom_prompt or get_system_prompt(
            lang,
            configurable.get("output_mode", "text_dsl"),
            prompt_version=prompt_version,
        )
        if not custom_prompt:
            app_registry = get_app_registry_summary(lang=lang)
            system_prompt = f"{system_prompt}\n\n{app_registry}"
        new_messages.append(MessageBuilder.create_system_message(system_prompt))

        screen_info = MessageBuilder.build_screen_info(current_app)
        text_content = f"{task}\n\n{screen_info}"
        marks_block = mark_registry.prompt_block(lang)
        if marks_block:
            text_content = f"{text_content}\n\n{marks_block}"
        if context_block:
            text_content = f"{text_content}\n\n{context_block}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content,
                image_base64=screenshot.base64_data,
                image_mime_type=getattr(screenshot, "mime_type", "image/png"),
            )
        )
    else:
        screen_info = MessageBuilder.build_screen_info(current_app)
        reflection_context = _build_reflection_context(state)
        if reflection_context:
            text_content = f"** Screen Info **\n\n{screen_info}\n\n{reflection_context}"
        else:
            text_content = f"** Screen Info **\n\n{screen_info}"
        marks_block = mark_registry.prompt_block(lang)
        if marks_block:
            text_content = f"{text_content}\n\n{marks_block}"
        if context_block:
            text_content = f"{text_content}\n\n{context_block}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content,
                image_base64=screenshot.base64_data,
                image_mime_type=getattr(screenshot, "mime_type", "image/png"),
            )
        )

    # 3. Model inference (pass full messages for context)
    full_messages = list(state["messages"]) + new_messages
    request_messages, context_selection = compact_messages_for_request(
        full_messages, context_selection
    )
    context_metrics = context_selection.metrics()
    parse_retry_limit = int(configurable.get("parse_retry", 1) or 0)
    request_parse_metadata = {}
    request_parse_error = None
    request_retry_count = 0
    try:
        response = _safe_request(model_client, request_messages)
    except ModelParseError as e:
        request_parse_metadata = getattr(e, "parse_metadata", {}) or {}
        request_parse_error = f"Model parse failed: {request_parse_metadata.get('parse_error_code') or 'parse_error'}"
        if parse_retry_limit > 0:
            request_retry_count = 1
            emit_trace(
                config,
                state,
                "plan",
                "parse_retry",
                {
                    "parse_retry_count": request_retry_count,
                    "parse_error_code": request_parse_metadata.get("parse_error_code"),
                },
            )
            try:
                response = _safe_request(
                    model_client,
                    _build_parse_retry_messages(request_messages, request_parse_error),
                )
                request_parse_metadata = {
                    **(getattr(response, "parse_metadata", {}) or {}),
                    "parse_retry_count": request_retry_count,
                    "parse_retry_success": True,
                }
            except Exception as retry_exc:
                if configurable.get("verbose", True):
                    print(f"Model parse retry failed: {type(retry_exc).__name__}")
                retry_metadata = getattr(retry_exc, "parse_metadata", {}) or {}
                parse_metadata = {
                    **request_parse_metadata,
                    **retry_metadata,
                    "parse_retry_count": request_retry_count,
                    "parse_retry_success": False,
                }
                error_message = f"Model parse failed: {parse_metadata.get('parse_error_code') or 'parse_error'}"
                emit_trace(
                    config,
                    state,
                    "plan",
                    "plan_error",
                    {
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
                    "screen_id": observation.snapshot.screen_id,
                    "screen_hash": observation.snapshot.screen_hash,
                    "observation": observation.to_dict(),
                    "mark_registry": mark_registry.to_dict(),
                    "screen_width": screenshot.width,
                    "screen_height": screenshot.height,
                    "thinking": "",
                    "action_raw": "",
                    "action_parsed": None,
                    "action_result": {"success": False, "should_finish": True, "message": error_message},
                    "error": error_message,
                    "failure_cause": "model_parse_failed",
                    "parse_metadata": parse_metadata,
                    "finished": True,
                    "action_confirmed": False,
                    "context_mode": context_mode,
                    **context_metrics,
                }
        else:
            parse_metadata = request_parse_metadata
            error_message = request_parse_error or "Model parse failed: parse_error"
            emit_trace(
                config,
                state,
                "plan",
                "plan_error",
                {
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
                "screen_id": observation.snapshot.screen_id,
                "screen_hash": observation.snapshot.screen_hash,
                "observation": observation.to_dict(),
                "mark_registry": mark_registry.to_dict(),
                "screen_width": screenshot.width,
                "screen_height": screenshot.height,
                "thinking": "",
                "action_raw": "",
                "action_parsed": None,
                "action_result": {"success": False, "should_finish": True, "message": error_message},
                "error": error_message,
                "failure_cause": "model_parse_failed",
                "parse_metadata": parse_metadata,
                "finished": True,
                "action_confirmed": False,
                "context_mode": context_mode,
                **context_metrics,
            }
    except Exception as e:
        if configurable.get("verbose", True):
            print(f"Model error: {type(e).__name__}")
        error_message = f"Model error: {type(e).__name__}"
        parse_metadata = getattr(e, "parse_metadata", {}) or {}
        emit_trace(
            config,
            state,
            "plan",
            "plan_error",
            {
                "message": error_message,
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
            "screen_id": observation.snapshot.screen_id,
            "screen_hash": observation.snapshot.screen_hash,
            "observation": observation.to_dict(),
            "mark_registry": mark_registry.to_dict(),
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

    # 4. Parse action, optionally ground IntentIR, then validate canonical IR before safety/execution.
    action_parsed, parse_error, parse_metadata, intent_raw, grounding_error = _parse_and_ground_response(
        response,
        configurable,
        mark_registry,
        observation.snapshot.screen_id,
    )
    retry_count = request_retry_count
    if parse_error and retry_count < parse_retry_limit:
        retry_count += 1
        emit_trace(
            config,
            state,
            "plan",
            "parse_retry",
            {
                "parse_retry_count": retry_count,
                "parse_error_code": parse_metadata.get("parse_error_code"),
                "grounding_error_code": parse_metadata.get("grounding_error_code"),
            },
        )
        retry_messages = _build_parse_retry_messages(request_messages, parse_error)
        try:
            retry_response = _safe_request(model_client, retry_messages)
            retry_parsed, retry_error, retry_metadata, retry_intent, retry_grounding_error = _parse_and_ground_response(
                retry_response,
                configurable,
                mark_registry,
                observation.snapshot.screen_id,
            )
            parse_metadata = {**retry_metadata, "parse_retry_count": retry_count, "parse_retry_success": retry_error is None}
            response = retry_response
            action_parsed = retry_parsed
            parse_error = retry_error
            intent_raw = retry_intent
            grounding_error = retry_grounding_error
        except Exception as exc:
            parse_metadata = {
                **parse_metadata,
                "parse_retry_count": retry_count,
                "parse_retry_success": False,
                "parse_retry_error_code": getattr(exc, "code", "retry_error"),
            }
    elif request_parse_metadata:
        parse_metadata = {**parse_metadata, **request_parse_metadata}

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
            "parse_retry_count": retry_count,
            "parse_retry_success": parse_metadata.get("parse_retry_success"),
            "grounding_error_code": parse_metadata.get("grounding_error_code"),
            "mark_registry": mark_registry.trace_summary(),
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
            "screen_id": observation.snapshot.screen_id,
            "screen_hash": observation.snapshot.screen_hash,
            "observation": observation.to_dict(),
            "mark_registry": mark_registry.to_dict(),
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "thinking": response.thinking,
            "action_raw": response.action,
            "action_parsed": None,
            "intent_raw": intent_raw,
            "grounding_error": grounding_error,
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
        "screen_id": observation.snapshot.screen_id,
        "screen_hash": observation.snapshot.screen_hash,
        "observation": observation.to_dict(),
        "mark_registry": mark_registry.to_dict(),
        "screen_width": screenshot.width,
        "screen_height": screenshot.height,
        "thinking": response.thinking,
        "action_raw": response.action,
        "action_parsed": action_parsed,
        "intent_raw": intent_raw,
        "grounding_error": grounding_error,
        "action_confirmed": False,
        "context_mode": context_mode,
        **context_metrics,
    }
