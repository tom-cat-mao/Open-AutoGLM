"""Plan node: screenshot → build messages → model inference → parse action."""

import json
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.actions.ir import is_intent_dict
from phone_agent.actions.repair import ActionRepairError, repair_action
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.config import get_prompt_version, get_system_prompt
from phone_agent.config.apps import get_app_registry_summary
from phone_agent.graph.context import (
    build_context_metrics,
    compact_messages_for_request,
    get_context_mode,
    _redacted_private_text,
    sanitize_context_payload,
    select_plan_context,
)
from phone_agent.graph.expected_outcome import (
    extract_provider_envelope,
    normalize_expected_outcome,
    sanitize_expected_outcome_dict,
)
from phone_agent.graph.observation import build_mark_provider_hints, build_observation
from phone_agent.graph.screenshot_status import (
    screenshot_failure_code,
    screenshot_failure_message,
    screenshot_is_sensitive,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.grounding.factory import build_mark_providers
from phone_agent.grounding.provider import ScreenBinding
from phone_agent.model.client import MessageBuilder
from phone_agent.model.client import ModelParseError

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def _build_reflection_context(state: "AgentState", *, consumer: str = "inject") -> str:
    reflection = state.get("reflection")
    task_context = state.get("task") if isinstance(state.get("task"), str) else None
    verdict = state.get("reflection_verdict")
    cause = state.get("failure_cause")
    strategy = state.get("suggested_strategy")
    parts = []
    if reflection:
        safe_reflection = sanitize_context_payload(
            reflection,
            "reflection",
            consumer=consumer,
            task_context=task_context,
        )
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


GROUNDING_ERROR_CODES = {
    "provider_unavailable",
    "unknown_mark",
    "mark_unavailable",
    "stale_mark",
    "stale_screen",
    "hash_mismatch",
    "mark_topology_mismatch",
    "low_confidence",
    "grounding_ambiguous",
    "grounding_no_candidate",
    "mark_required",
    "missing_hint",
    "mark_generation_failed",
    "target_required",
    "bad_bbox",
    "missing_provider_hash",
    "screen_binding_missing",
    "screenshot_unavailable",
    "secure_screenshot_blocked",
    "adb_screencap_failed",
    "screenshot_pull_failed",
    "invalid_screenshot",
}

VALIDATION_ERROR_CODES = {
    "invalid_metadata",
    "missing_field",
    "unknown_action",
    "unknown_app",
    "unsafe_value",
}


def _layer_for_error(code: str | None, grounding_error: str | None = None) -> str:
    if grounding_error or code in GROUNDING_ERROR_CODES:
        return "grounding"
    if code in VALIDATION_ERROR_CODES:
        return "validation"
    if code in {"invalid_json", "unsupported_tool_call", "parse_error"}:
        return "adapter" if code != "parse_error" else "parse"
    return "parse"


def _retry_policy_for_layer(layer: str) -> str:
    if layer in {"parse", "adapter"}:
        return "parse_retry"
    if layer == "grounding":
        return "reobserve"
    return "none"


def _error_fields(code: str | None, grounding_error: str | None = None) -> dict:
    layer = _layer_for_error(code, grounding_error)
    return {
        "error_layer": layer,
        "error_code": grounding_error or code or "unknown",
        "recoverable": layer in {"parse", "adapter", "grounding"},
        "retry_policy": _retry_policy_for_layer(layer),
    }


def _failure_cause_for_layer(error_fields: dict, parse_error: str | None) -> str | None:
    """Map layered errors to stable failure causes for state/trace."""

    if not parse_error:
        return None
    layer = error_fields.get("error_layer")
    if layer == "grounding":
        return error_fields.get("error_code") or "grounding_failed"
    if layer == "validation":
        return "action_validation_failed"
    if layer == "adapter":
        return "action_adapter_failed"
    return "model_parse_failed"


def _screenshot_error_fields(code: str, sensitive: bool = False) -> dict:
    return {
        "error_layer": "grounding",
        "error_code": code,
        "recoverable": True,
        "retry_policy": "takeover" if sensitive or code == "secure_screenshot_blocked" else "reobserve",
    }


def _failure_cause_for_screenshot(code: str, sensitive: bool = False) -> str:
    if sensitive or code == "secure_screenshot_blocked":
        return "unsafe_or_sensitive"
    return "context_lost"


def _parse_and_ground_response(
    response,
    configurable: dict,
    mark_registry,
    screen_binding: ScreenBinding,
):
    """Parse provider response, optionally ground IntentIR, then validate canonical ActionIR."""

    parse_error = None
    parse_metadata = getattr(response, "parse_metadata", {}) or {}
    intent_raw = None
    raw_expected_outcome = None
    grounding_error = None
    grounding_observation: dict = {}
    structured_json_response = False
    try:
        stripped_action = response.action.strip()
        structured_json_response = stripped_action.startswith("{")
        if stripped_action.startswith("{"):
            provider_payload = json.loads(stripped_action)
            action_payload, raw_expected_outcome = extract_provider_envelope(provider_payload)
            action_parsed = adapt_json_action(action_payload)
        else:
            raise ActionAdapterError(
                "invalid_json", "structured action execution requires JSON or provider tool_calls"
            )
    except json.JSONDecodeError as exc:
        action_parsed = None
        parse_error = "Model parse failed: invalid_json: payload is not valid JSON"
        parse_metadata = {
            **parse_metadata,
            "parse_success": False,
            "parse_error_code": "invalid_json",
        }
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
                screen_id=screen_binding.screen_id,
                screen_binding=screen_binding,
                timeout=float(configurable.get("grounding_timeout", 10.0) or 10.0),
                grounding_metadata=grounding_observation,
            )
            parse_metadata = {
                **parse_metadata,
                "intent_detected": True,
                "grounding_success": True,
                "target_mark_id": intent_raw.get("target_mark_id"),
                "grounding_provider": "mark_registry",
                "grounding_screen_hash": screen_binding.raw_screenshot_hash,
            }
        except GroundingError as exc:
            action_parsed = None
            grounding_error = exc.code
            parse_error = f"Grounding failed: {exc.code}: {exc}"
            parse_metadata = {
                **parse_metadata,
                "intent_detected": True,
                "grounding_success": False,
                "grounding_error_code": exc.code,
                "grounding_observation": grounding_observation,
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
        parse_error = "Grounding failed: mark_required: structured screen targeting requires target_mark_id"
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
    expected_outcome = normalize_expected_outcome(
        raw_expected_outcome,
        action=action_parsed,
        intent=intent_raw,
    )
    expected_outcome_dict = sanitize_expected_outcome_dict(
        expected_outcome,
        task_context=configurable.get("task_context"),
    )
    parse_metadata = {
        **parse_metadata,
        "expected_outcome_present": raw_expected_outcome is not None,
        "expected_outcome_kind": expected_outcome.kind,
    }
    return (
        action_parsed,
        parse_error,
        parse_metadata,
        intent_raw,
        grounding_error,
        grounding_observation,
        expected_outcome_dict,
    )


def _action_for_history(action: dict | None) -> dict | None:
    """Return a display-safe action copy for message history/state action_raw."""

    if not isinstance(action, dict):
        return action
    safe = dict(action)
    for key in ("text", "message"):
        if isinstance(safe.get(key), str):
            safe[key] = _redacted_private_text(safe[key])
    return safe


def _recovery_action_for_parse_failure(state: "AgentState", parse_metadata: dict) -> dict[str, Any] | None:
    if parse_metadata.get("parse_error_code") not in {"invalid_json", "parse_error"}:
        return None
    if state.get("failure_cause") != "wrong_page":
        return None
    if state.get("suggested_strategy") != "go_back":
        return None
    return {"_metadata": "do", "action": "Back"}


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
    screenshot_error = screenshot_failure_code(screenshot)
    if screenshot_error:
        sensitive = screenshot_is_sensitive(screenshot)
        context_mode = get_context_mode(state, config)
        failure_message = screenshot_failure_message(screenshot)
        error_message = f"Screenshot unavailable: {screenshot_error}"
        error_fields = _screenshot_error_fields(screenshot_error, sensitive=sensitive)
        failure_cause = _failure_cause_for_screenshot(screenshot_error, sensitive=sensitive)
        context_metrics = build_context_metrics(
            {
                **state,
                "context_mode": context_mode,
                "context_strategy": state.get("context_strategy") or (
                    "inject_redacted_block"
                    if context_mode == "inject"
                    else ("observe_only" if context_mode == "observe" else "off")
                ),
            }
        )
        grounding_observation = {
            "provider": "screenshot",
            "success": False,
            "failure_code": screenshot_error,
            "message": failure_message,
            "sensitive": sensitive,
        }
        emit_trace(
            config,
            state,
            "plan",
            "plan_error",
            {
                "message": error_message,
                "failure_cause": failure_cause,
                "grounding_error_code": screenshot_error,
                "grounding_observation": grounding_observation,
                **error_fields,
                **context_metrics,
            },
        )
        return {
            "messages": [],
            "step_count": step_count + 1,
            "screenshot_b64": None,
            "current_app": current_app,
            "screen_width": int(getattr(screenshot, "width", 0) or state.get("screen_width") or 0),
            "screen_height": int(getattr(screenshot, "height", 0) or state.get("screen_height") or 0),
            "thinking": "",
            "action_raw": "",
            "action_parsed": None,
            "intent_raw": None,
            "grounding_error": screenshot_error,
            "grounding_result": grounding_observation,
            "grounding_provider": "screenshot",
            "grounding_failure_code": screenshot_error,
            "grounding_observation": grounding_observation,
            "action_result": {
                "success": False,
                "should_finish": True,
                "message": error_message,
            },
            "error": error_message,
            "failure_cause": failure_cause,
            **error_fields,
            "finished": True,
            "action_confirmed": False,
            "context_mode": context_mode,
            **context_metrics,
        }
    screen_marks = configurable.get("screen_marks")
    accessibility_enabled = configurable.get("accessibility_marks")
    if accessibility_enabled is None:
        import os

        accessibility_enabled = os.getenv("PHONE_AGENT_ACCESSIBILITY_MARKS", "").lower() in {"1", "true", "yes", "on"}
    provider_name = str(configurable.get("grounding_provider_name") or "").lower()
    hybrid_provider_enabled = provider_name in {"hybrid", "accessibility_locateanything", "uiautomator_locateanything"}
    if screen_marks is None and accessibility_enabled and not hybrid_provider_enabled and hasattr(device_factory, "get_screen_marks"):
        try:
            screen_marks = device_factory.get_screen_marks(
                device_id,
                width=screenshot.width,
                height=screenshot.height,
                timeout=float(configurable.get("accessibility_timeout", 3.0) or 3.0),
                max_marks=int(configurable.get("accessibility_max_marks", 80) or 80),
            )
        except Exception as exc:
            screen_marks = None
            emit_trace(
                config,
                state,
                "plan",
                "accessibility_marks_error",
                {"failure_code": type(exc).__name__, "message": "accessibility marks unavailable"},
            )
    provider_configurable = dict(configurable)
    if (
        provider_configurable.get("accessibility_tree_dump") is None
        and not provider_configurable.get("skip_accessibility_provider")
        and hasattr(device_factory, "module")
        and hasattr(device_factory.module, "dump_uiautomator_xml")
    ):
        provider_configurable["accessibility_tree_dump"] = lambda timeout=None: device_factory.module.dump_uiautomator_xml(
            device_id,
            timeout=timeout,
        )
    mark_provider_hints = build_mark_provider_hints(
        task=task,
        reflection=state.get("reflection"),
        provider_hints=configurable.get("mark_provider_hints") or configurable.get("grounding_hints"),
    )
    observation = build_observation(
        screenshot=screenshot,
        current_app=current_app,
        marks=screen_marks,
        mark_providers=build_mark_providers(provider_configurable),
        provider_hints=mark_provider_hints,
        provider_timeout=float(configurable.get("grounding_timeout", 10.0) or 10.0),
    )
    screen_binding = ScreenBinding(
        screen_id=observation.snapshot.screen_id,
        raw_screenshot_hash=observation.snapshot.raw_screenshot_hash,
        width=screenshot.width,
        height=screenshot.height,
        current_app=current_app,
        semantic_screen_id=observation.snapshot.semantic_screen_id,
        observation_epoch=observation.snapshot.observation_epoch,
        mark_set_version=observation.snapshot.mark_set_version,
        perceptual_hash=observation.snapshot.perceptual_hash,
    )
    mark_registry = observation.mark_registry
    context_mode = get_context_mode(state, config)
    prompt_version = get_prompt_version(configurable.get("prompt_version"))
    configured_output_mode = configurable.get("output_mode", "json_schema")
    if configured_output_mode not in {"json_schema", "tool_calls", "auto"}:
        raise ValueError("output_mode must be one of: json_schema, tool_calls, auto")
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
        {
            "task": task,
            "current_app": current_app,
            "mark_provider_observation": observation.mark_provider_observation,
            **context_metrics,
        },
    )

    # 2. Build new messages (only the new ones, reducer will append)
    new_messages = []
    if step_count == 0:
        custom_prompt = configurable.get("system_prompt")
        system_prompt = custom_prompt or get_system_prompt(
            lang,
            configured_output_mode,
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
        response = _safe_request(model_client, request_messages, output_mode=configured_output_mode)
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
                    output_mode=configured_output_mode,
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
                error_fields = _error_fields(parse_metadata.get("parse_error_code") or "parse_error")
                emit_trace(
                    config,
                    state,
                    "plan",
                    "plan_error",
                    {
                        "failure_cause": "model_parse_failed",
                        "parse_metadata": parse_metadata,
                        "parse_error_code": parse_metadata.get("parse_error_code"),
                        **error_fields,
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
                    **error_fields,
                    "parse_metadata": parse_metadata,
                    "finished": True,
                    "action_confirmed": False,
                    "context_mode": context_mode,
                    **context_metrics,
                }
        else:
            parse_metadata = request_parse_metadata
            error_message = request_parse_error or "Model parse failed: parse_error"
            error_fields = _error_fields(parse_metadata.get("parse_error_code") or "parse_error")
            emit_trace(
                config,
                state,
                "plan",
                "plan_error",
                {
                    "failure_cause": "model_parse_failed",
                    "parse_metadata": parse_metadata,
                    "parse_error_code": parse_metadata.get("parse_error_code"),
                    **error_fields,
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
                **error_fields,
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
        error_fields = {
            "error_layer": "adapter",
            "error_code": parse_metadata.get("parse_error_code") or "model_request_failed",
            "recoverable": True,
            "retry_policy": "parse_retry",
        }
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
                **error_fields,
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
            **error_fields,
            "parse_metadata": parse_metadata,
            "finished": True,
            "action_confirmed": False,
            "context_mode": context_mode,
            **context_metrics,
        }

    # 4. Parse action, optionally ground IntentIR, then validate canonical IR before safety/execution.
    parse_configurable = {**configurable, "task_context": task}
    (
        action_parsed,
        parse_error,
        parse_metadata,
        intent_raw,
        grounding_error,
        grounding_observation,
        expected_outcome,
    ) = _parse_and_ground_response(
        response,
        parse_configurable,
        mark_registry,
        screen_binding,
    )
    retry_count = request_retry_count
    current_error_layer = _layer_for_error(parse_metadata.get("parse_error_code"), grounding_error)
    if parse_error and current_error_layer in {"parse", "adapter"} and retry_count < parse_retry_limit:
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
            retry_response = _safe_request(model_client, retry_messages, output_mode=configured_output_mode)
            (
                retry_parsed,
                retry_error,
                retry_metadata,
                retry_intent,
                retry_grounding_error,
                retry_grounding_observation,
                retry_expected_outcome,
            ) = _parse_and_ground_response(
                retry_response,
                parse_configurable,
                mark_registry,
                screen_binding,
            )
            parse_metadata = {**retry_metadata, "parse_retry_count": retry_count, "parse_retry_success": retry_error is None}
            response = retry_response
            action_parsed = retry_parsed
            parse_error = retry_error
            intent_raw = retry_intent
            grounding_error = retry_grounding_error
            grounding_observation = retry_grounding_observation
            expected_outcome = retry_expected_outcome
        except Exception as exc:
            parse_metadata = {
                **parse_metadata,
                "parse_retry_count": retry_count,
                "parse_retry_success": False,
                "parse_retry_error_code": getattr(exc, "code", "retry_error"),
            }
    elif request_parse_metadata:
        parse_metadata = {**parse_metadata, **request_parse_metadata}

    error_fields = _error_fields(parse_metadata.get("parse_error_code"), grounding_error) if parse_error else {
        "error_layer": None,
        "error_code": None,
        "recoverable": None,
        "retry_policy": None,
    }
    grounding_candidates = grounding_observation.get("candidates") or []
    grounding_candidate_count = int(grounding_observation.get("candidate_count") or len(grounding_candidates) or 0)
    selected_grounding_candidate_id = grounding_observation.get("selected_candidate_id")
    action_raw_payload: dict = {
        "action": _action_for_history(action_parsed) if action_parsed is not None else None,
        "parse_success": parse_error is None,
    }
    if expected_outcome is not None:
        action_raw_payload["expected_outcome"] = expected_outcome
    action_raw_safe = json.dumps(action_raw_payload, ensure_ascii=False)
    thinking_safe = str(
        sanitize_context_payload(
            response.thinking,
            consumer="trace_payload",
            task_context=task,
        )
    )

    emit_trace(
        config,
        state,
        "plan",
        "plan_result",
        {
            "current_app": current_app,
            "thinking": thinking_safe,
            "action_raw": action_raw_safe,
            "action": action_parsed.get("action") if isinstance(action_parsed, dict) else None,
            "metadata": action_parsed.get("_metadata") if isinstance(action_parsed, dict) else None,
            "parse_success": parse_error is None,
            "parse_error": parse_error,
            "failure_cause": _failure_cause_for_layer(error_fields, parse_error),
            **error_fields,
            "parse_metadata": parse_metadata,
            "validation_success": parse_metadata.get("validation_success"),
            "validation_error_code": parse_metadata.get("validation_error_code"),
            "parse_retry_count": retry_count,
            "parse_retry_success": parse_metadata.get("parse_retry_success"),
            "grounding_error_code": parse_metadata.get("grounding_error_code"),
            "grounding_observation": grounding_observation,
            "mark_provider_observation": observation.mark_provider_observation,
            "grounding_candidate_count": grounding_candidate_count,
            "selected_grounding_candidate_id": selected_grounding_candidate_id,
            "expected_outcome": expected_outcome,
            "mark_registry": mark_registry.trace_summary(),
            "repair_attempted": parse_metadata.get("repair_attempted", False),
            "repair_success": parse_metadata.get("repair_success"),
            "repair_error_code": parse_metadata.get("repair_error_code"),
            **context_metrics,
        },
    )

    if parse_error:
        recovery_action = _recovery_action_for_parse_failure(state, parse_metadata)
        if recovery_action is not None:
            recovery_raw = json.dumps(
                {
                    "action": recovery_action,
                    "parse_success": False,
                    "recovery_from": parse_metadata.get("parse_error_code"),
                },
                ensure_ascii=False,
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
                "thinking": thinking_safe,
                "action_raw": recovery_raw,
                "action_parsed": recovery_action,
                "intent_raw": intent_raw,
                "grounding_error": grounding_error,
                "grounding_result": grounding_observation or None,
                "grounding_provider": grounding_observation.get("provider"),
                "grounding_latency_ms": grounding_observation.get("latency_ms"),
                "grounding_failure_code": grounding_error,
                "grounding_screen_hash": grounding_observation.get("raw_screenshot_hash") or observation.snapshot.screen_hash,
                "grounding_observation": grounding_observation or None,
                "mark_provider_observation": observation.mark_provider_observation,
                "grounding_candidates": grounding_candidates,
                "grounding_candidate_count": grounding_candidate_count,
                "selected_grounding_candidate_id": selected_grounding_candidate_id,
                "expected_outcome": expected_outcome,
                "failure_cause": "model_parse_failed",
                **error_fields,
                "parse_metadata": {
                    **parse_metadata,
                    "deterministic_recovery_action": "Back",
                    "deterministic_recovery_reason": "wrong_page_go_back",
                },
                "finished": False,
                "error": None,
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
            "thinking": thinking_safe,
            "action_raw": action_raw_safe,
            "action_parsed": None,
            "intent_raw": intent_raw,
            "grounding_error": grounding_error,
            "grounding_result": grounding_observation or None,
            "grounding_provider": grounding_observation.get("provider"),
            "grounding_latency_ms": grounding_observation.get("latency_ms"),
            "grounding_failure_code": grounding_error,
            "grounding_screen_hash": grounding_observation.get("raw_screenshot_hash") or observation.snapshot.screen_hash,
            "grounding_observation": grounding_observation or None,
            "mark_provider_observation": observation.mark_provider_observation,
            "grounding_candidates": grounding_candidates,
            "grounding_candidate_count": grounding_candidate_count,
            "selected_grounding_candidate_id": selected_grounding_candidate_id,
            "expected_outcome": expected_outcome,
            "action_result": {
                "success": False,
                "should_finish": True,
                "message": parse_error,
            },
            "error": parse_error,
            "failure_cause": _failure_cause_for_layer(error_fields, parse_error),
            **error_fields,
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
        "thinking": thinking_safe,
        "action_raw": action_raw_safe,
        "action_parsed": action_parsed,
        "intent_raw": intent_raw,
        "grounding_error": grounding_error,
        "grounding_result": grounding_observation or None,
        "grounding_provider": grounding_observation.get("provider"),
        "grounding_latency_ms": grounding_observation.get("latency_ms"),
        "grounding_failure_code": grounding_error,
        "grounding_screen_hash": grounding_observation.get("raw_screenshot_hash") or observation.snapshot.screen_hash,
        "grounding_observation": grounding_observation or None,
        "mark_provider_observation": observation.mark_provider_observation,
        "grounding_candidates": grounding_candidates,
        "grounding_candidate_count": grounding_candidate_count,
        "selected_grounding_candidate_id": selected_grounding_candidate_id,
        "expected_outcome": expected_outcome,
        **error_fields,
        "action_confirmed": False,
        "context_mode": context_mode,
        **context_metrics,
    }
