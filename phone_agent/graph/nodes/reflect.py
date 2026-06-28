"""Reflect node: screenshot → structured action outcome reflection."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    build_action_outcome_summary,
    build_screen_belief,
    context_enabled,
    detect_repeated_failure,
    get_context_mode,
    normalize_failure_cause,
    sanitize_context_payload,
    _redacted_private_text,
    update_gui_memory,
    update_failure_memory,
    update_summarized_history,
)
from phone_agent.graph.expected_outcome import expected_outcome_prompt_block
from phone_agent.graph.observation import build_mark_provider_hints, build_observation
from phone_agent.graph.screenshot_status import (
    screenshot_failure_code,
    screenshot_failure_message,
    screenshot_is_sensitive,
)
from phone_agent.graph.task_goal import (
    ensure_task_goal_contract,
    task_goal_prompt_block,
    task_goal_trace_payload,
    validate_finish_claim,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.graph.verifier import merge_verifier_with_reflection, verify_action_outcome
from phone_agent.grounding.factory import build_mark_providers
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效，并给出下一步建议。

你必须只输出一个 JSON 对象，不要 Markdown、XML、函数调用或多余文本：
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx"}

判断标准：
1. 动作生效：页面满足预期后置条件（如输入框聚焦、目标文本出现、目标页面打开、目标应用打开）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"任务已完成"}

重要约束：
- 只有在截图明确显示加载中、空白页、网络错误、进度条/转圈、或执行结果表示应用无响应时，才使用 failure_cause="network_or_loading" 和 suggested_strategy="wait"。
- 如果刚执行的是 Launch/启动应用，且当前屏幕信息或截图已显示目标应用/设置页/目标页面已打开，即使任务还没完成，也应判定为 succeeded + continue，而不是 partial + wait。
- 不要因为页面内容很多、设置项列表尚需下一步操作，就误判为加载中；可继续操作的稳定页面应输出 continue。
- 广告、banner、推荐流、热词、计数器或首页动态内容变化只能作为噪声，不能单独证明 Tap/Type/搜索/打开视频成功；必须引用后置条件证据。
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded, then give next-step advice.

You MUST output exactly one JSON object. Do not output Markdown, XML, function calls, or extra text:
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx"}

Judgment criteria:
1. Action succeeded: expected postconditions are satisfied (for example focused input, expected text, target page, or target app)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed"}

Important constraints:
- Use failure_cause="network_or_loading" and suggested_strategy="wait" only when the screenshot clearly shows loading, a blank page, a network error, a spinner/progress indicator, or the execution result indicates the app is not responding.
- If the action just executed is Launch and the current screen info or screenshot already shows the target app/settings/target page is open, judge it as succeeded + continue even if the overall task still needs more steps; do not return partial + wait.
- Do not treat a stable page with many settings/list items as loading. If the page is actionable, return continue.
- Ads, banners, recommendation feeds, hot words, counters, or dynamic home-page content changes are noise and cannot alone prove Tap/Type/search/video success; cite postcondition evidence.
"""

VALID_VERDICTS = {"succeeded", "failed", "partial"}
VALID_FAILURE_CAUSES = FAILURE_TAXONOMY
VALID_STRATEGIES = {
    "continue",
    "retry",
    "retry_with_offset",
    "go_back",
    "swipe_to_find",
    "wait",
    "takeover",
    "finish",
}
VERIFIED_REFLECTION_SKIP_CONFIDENCE = 0.9
DEFAULT_TAKEOVER_RETRY_THRESHOLD = 3


@dataclass
class ReflectionResult:
    verdict: str
    failure_cause: str | None
    suggested_strategy: str | None
    message: str
    has_evidence: bool = False


def _reflection_from_verifier(verifier_result) -> ReflectionResult | None:
    """Return a deterministic reflection when verifier is conclusive."""

    if verifier_result.hard_failure:
        return ReflectionResult(
            "failed",
            verifier_result.failure_cause or "unknown",
            "retry",
            "deterministic verifier hard failure",
            True,
        )
    if verifier_result.status == "success" and verifier_result.confidence >= VERIFIED_REFLECTION_SKIP_CONFIDENCE:
        return ReflectionResult(
            "succeeded",
            None,
            "continue",
            "deterministic postconditions matched",
            True,
        )
    if verifier_result.status == "failure" and verifier_result.confidence >= 0.7:
        return ReflectionResult(
            "failed",
            verifier_result.failure_cause or "unknown",
            "retry",
            "deterministic postconditions missing",
            True,
        )
    return None


def parse_reflection_action(raw_action: str) -> ReflectionResult:
    """Parse structured JSON reflection output."""
    raw_action = raw_action.strip()
    try:
        data = json.loads(raw_action)
    except json.JSONDecodeError:
        return ReflectionResult("failed", "unknown", "retry", raw_action)
    if not isinstance(data, dict):
        return ReflectionResult("failed", "unknown", "retry", raw_action)
    if "verdict" in data:
        verdict = str(data.get("verdict", "failed"))
        failure_cause = str(data.get("failure_cause", "unknown"))
        suggested_strategy = str(data.get("suggested_strategy", "retry"))
        message = str(data.get("message", raw_action))
        has_evidence = False
    else:
        action_effect = str(data.get("action_effect", "unknown"))
        dynamic_only = data.get("dynamic_change_only") is True
        missing = data.get("missing_postconditions")
        matched = data.get("matched_postconditions")
        evidence = data.get("evidence")
        has_missing = bool(missing)
        has_positive_evidence = bool(matched) or bool(evidence)
        has_evidence = has_positive_evidence
        if action_effect in {"succeeded", "success"} and not dynamic_only and not has_missing and has_positive_evidence:
            verdict = "succeeded"
            failure_cause = "none"
            suggested_strategy = str(data.get("next_strategy", "continue"))
        elif action_effect in {"succeeded", "success"} and (dynamic_only or has_missing):
            verdict = "failed"
            failure_cause = "wrong_page" if has_missing else "unknown"
            suggested_strategy = str(data.get("next_strategy", "retry"))
        elif action_effect in {"partial", "unknown"}:
            verdict = "partial"
            failure_cause = "unknown"
            suggested_strategy = str(data.get("next_strategy", "retry"))
        else:
            verdict = "failed"
            failure_cause = "wrong_page" if missing else "unknown"
            suggested_strategy = str(data.get("next_strategy", "retry"))
        message = str(data.get("evidence") or data.get("task_progress") or raw_action)
        task_progress = str(data.get("task_progress") or "").lower()
        if suggested_strategy == "finish" and (
            "not finished" in task_progress
            or "unfinished" in task_progress
            or "未完成" in task_progress
            or "尚未完成" in task_progress
        ):
            verdict = "partial"
            failure_cause = "unknown"
            suggested_strategy = "continue"
    if verdict not in VALID_VERDICTS:
        verdict = "failed"
    failure_cause = normalize_failure_cause(failure_cause)
    if suggested_strategy not in VALID_STRATEGIES:
        suggested_strategy = "retry"
    parsed_cause = None if verdict == "succeeded" and failure_cause == "none" else failure_cause
    return ReflectionResult(verdict, parsed_cause, suggested_strategy, message, has_evidence)


def _screenshot_failure_update(
    *,
    state: "AgentState",
    config: RunnableConfig,
    screenshot: object,
    current_app: str,
    context_mode: str,
) -> dict:
    """Build a terminal reflect update when a screenshot is unavailable."""

    code = screenshot_failure_code(screenshot) or "screenshot_unavailable"
    sensitive = screenshot_is_sensitive(screenshot)
    failure_cause = "unsafe_or_sensitive" if sensitive or code == "secure_screenshot_blocked" else "context_lost"
    suggested_strategy = "takeover" if failure_cause == "unsafe_or_sensitive" else "retry"
    error_fields = {
        "error_layer": "grounding",
        "error_code": code,
        "recoverable": True,
        "retry_policy": "takeover" if failure_cause == "unsafe_or_sensitive" else "reobserve",
    }
    error_message = f"Screenshot unavailable: {code}"
    result_dict = {"success": False, "should_finish": True, "message": error_message}
    emit_trace(
        config,
        state,
        "reflect",
        "reflect_error",
        {
            "message": error_message,
            "failure_cause": failure_cause,
            "grounding_error_code": code,
            **error_fields,
        },
    )
    return {
        "screenshot_b64": None,
        "current_app": current_app,
        "action_result": result_dict,
        "reflection": error_message,
        "action_succeeded": False,
        "reflection_verdict": "failed",
        "failure_cause": failure_cause,
        "suggested_strategy": suggested_strategy,
        "grounding_error": code,
        "grounding_failure_code": code,
        "grounding_provider": "screenshot",
        "retry_count": int(state.get("retry_count") or 0) + 1,
        "finished": True,
        "error": error_message,
        "context_mode": context_mode,
        **error_fields,
    }


def _build_after_observation(
    *,
    state: "AgentState",
    config: RunnableConfig,
    screenshot: object,
    current_app: str,
    device_factory: object,
    device_id: str | None,
) -> object:
    """Build a fresh after-action observation for postcondition verification."""

    configurable = config.get("configurable", {})
    screen_marks = configurable.get("after_screen_marks")
    if screen_marks is None:
        screen_marks = configurable.get("screen_marks_after")
    accessibility_enabled = configurable.get("accessibility_marks")
    if accessibility_enabled is None:
        import os

        accessibility_enabled = os.getenv("PHONE_AGENT_ACCESSIBILITY_MARKS", "").lower() in {"1", "true", "yes", "on"}
    provider_name = str(configurable.get("grounding_provider_name") or "").lower()
    hybrid_provider_enabled = provider_name in {"hybrid", "accessibility_locateanything", "uiautomator_locateanything"}
    if (
        screen_marks is None
        and accessibility_enabled
        and not hybrid_provider_enabled
        and hasattr(device_factory, "get_screen_marks")
    ):
        try:
            screen_marks = device_factory.get_screen_marks(
                device_id,
                width=getattr(screenshot, "width", 0),
                height=getattr(screenshot, "height", 0),
                timeout=float(configurable.get("accessibility_timeout", 3.0) or 3.0),
                max_marks=int(configurable.get("accessibility_max_marks", 80) or 80),
            )
        except Exception as exc:
            screen_marks = None
            emit_trace(
                config,
                state,
                "reflect",
                "after_accessibility_marks_error",
                {"failure_code": type(exc).__name__, "message": "after accessibility marks unavailable"},
            )

    provider_configurable = dict(configurable)
    if not provider_configurable.get("reflect_enable_vlm_grounding"):
        provider_configurable["grounding_provider_name"] = "accessibility"
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
    provider_hints = build_mark_provider_hints(
        task=state.get("task"),
        reflection=state.get("reflection"),
        provider_hints=configurable.get("mark_provider_hints") or configurable.get("grounding_hints"),
    )
    return build_observation(
        screenshot=screenshot,
        current_app=current_app,
        marks=screen_marks,
        mark_providers=build_mark_providers(provider_configurable),
        provider_hints=provider_hints,
        provider_timeout=float(configurable.get("grounding_timeout", 10.0) or 10.0),
    )


def _bounded_observation_summary(payload: dict | None, *, task_context: str | None = None) -> dict:
    """Return prompt/trace-safe screen evidence without raw screenshots or trees."""

    if not isinstance(payload, dict):
        return {}
    safe = sanitize_context_payload(payload, consumer="reflect_prompt", task_context=task_context)
    if not isinstance(safe, dict):
        return {}
    marks = safe.get("marks")
    if isinstance(marks, list):
        safe["marks"] = marks[:20]
    return safe


def _state_before_observation_payload(state: "AgentState", *, task_context: str | None = None) -> dict:
    observation = state.get("observation")
    if not isinstance(observation, dict):
        return {}
    snapshot = observation.get("snapshot") if isinstance(observation.get("snapshot"), dict) else {}
    registry = observation.get("mark_registry") if isinstance(observation.get("mark_registry"), dict) else {}
    marks_value = registry.get("marks") if isinstance(registry, dict) else []
    if isinstance(marks_value, dict):
        iterable_marks = marks_value.values()
    elif isinstance(marks_value, list):
        iterable_marks = marks_value
    else:
        iterable_marks = []
    marks = []
    for mark in iterable_marks:
        if not isinstance(mark, dict):
            continue
        marks.append(
            {
                "mark_id": mark.get("mark_id"),
                "role": mark.get("role"),
                "text_summary": sanitize_context_payload(
                    mark.get("text_summary") or "",
                    consumer="trace_payload",
                    task_context=task_context,
                ),
            }
        )
    payload = {
        "snapshot": {
            key: snapshot.get(key)
            for key in ("screen_id", "screen_hash", "current_app", "semantic_screen_id", "mark_set_version")
            if snapshot.get(key) is not None
        },
        "marks": marks,
        "mark_provider_observation": observation.get("mark_provider_observation"),
    }
    return _bounded_observation_summary(payload, task_context=task_context)


def _collect_device_verifier_signals(
    *,
    device_factory: object,
    device_id: str | None,
    config: RunnableConfig,
) -> dict:
    """Collect optional read-only post-action signals for verifier use."""

    configurable = config.get("configurable", {})
    signals: dict[str, object] = {}

    for config_key, output_key in (
        ("focused_editable", "focused_editable"),
        ("focused_window", "focused_window"),
        ("top_activity", "top_activity"),
        ("keyboard_visible", "keyboard_visible"),
    ):
        if config_key in configurable:
            signals[output_key] = configurable[config_key]

    module = getattr(device_factory, "module", None)
    for owner in (device_factory, module):
        if owner is None:
            continue
        if "focused_window" not in signals and hasattr(owner, "get_focused_window_or_app"):
            try:
                signals["focused_window"] = owner.get_focused_window_or_app(device_id)
            except Exception:
                pass
        if "top_activity" not in signals and hasattr(owner, "get_top_activity"):
            try:
                signals["top_activity"] = owner.get_top_activity(device_id)
            except Exception:
                pass
        if "keyboard_visible" not in signals and hasattr(owner, "is_keyboard_visible"):
            try:
                signals["keyboard_visible"] = bool(owner.is_keyboard_visible(device_id))
            except Exception:
                pass
    return signals


def _verifier_observation_payload(observation, *, task_context: str | None = None) -> dict:
    """Build after-observation text for in-memory verifier matching only."""

    marks = []
    for mark in observation.mark_registry.marks.values():
        row = {
            "mark_id": mark.mark_id,
            "role": mark.role,
            "text_summary": mark.text_summary or "",
        }
        marks.append(row)
    return {
        "snapshot": observation.snapshot.to_dict(),
        "marks": marks,
        "mark_provider_observation": observation.mark_provider_observation,
    }


def _sanitize_verifier_observation_payload(payload: dict, *, task_context: str | None = None) -> dict:
    """Return prompt/trace-safe verifier observation without raw UI text."""

    if not isinstance(payload, dict):
        return {}
    safe = sanitize_context_payload(payload, consumer="checkpoint", task_context=task_context)
    if not isinstance(safe, dict):
        return {}
    marks = safe.get("marks")
    if isinstance(marks, list):
        safe["marks"] = marks[:20]
    return safe


SAFE_VERIFIER_EVIDENCE_STRINGS = {
    "after_observation_unavailable",
    "app_opened",
    "content_shift_unverified",
    "focused_editable_or_keyboard_visible",
    "input_focused",
    "input_progress",
    "postcondition_unverified",
    "private_text_unverifiable",
    "typed_text_present",
}


def _sanitize_verifier_evidence(evidence: dict, *, task_context: str | None = None) -> dict:
    """Sanitize verifier evidence while preserving stable machine codes."""

    safe = sanitize_context_payload(evidence, consumer="trace_payload", task_context=task_context)
    if not isinstance(safe, dict):
        return {}
    for key in ("matched_postconditions", "missing_postconditions"):
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if isinstance(value, list):
            safe[key] = [_sanitize_postcondition_item(item) for item in value]
    return safe


def _sanitize_postcondition_item(item):
    if not isinstance(item, str):
        return sanitize_context_payload(item, consumer="trace_payload")
    if item in SAFE_VERIFIER_EVIDENCE_STRINGS or item.startswith("sha256:") or item.startswith("forbidden:sha256:"):
        return item
    return _redacted_private_text(item)


def _sanitize_verifier_result_dict(verifier_result, *, task_context: str | None = None) -> dict:
    data = verifier_result.to_dict()
    if isinstance(data.get("evidence"), dict):
        data["evidence"] = _sanitize_verifier_evidence(data["evidence"], task_context=task_context)
    if isinstance(data.get("signals"), dict):
        data["signals"] = sanitize_context_payload(data["signals"], consumer="checkpoint", task_context=task_context)
    return data


def _maybe_emit_reflect_prompt_debug(
    config: RunnableConfig,
    state: "AgentState",
    *,
    reflect_messages: list[dict],
    reflect_text: str,
    expected_outcome_text: str,
    verifier_signals: str,
    before_summary: str,
    after_summary: str,
    screen_info: str,
) -> None:
    """Emit opt-in reflect request debug traces for local diagnosis."""

    configurable = config.get("configurable", {}) if config else {}
    reflect_debug_messages = _strip_images_for_reflect_prompt_debug(reflect_messages)
    payload: dict[str, object] = {
        "request_message_count": len(reflect_messages),
        "request_message_roles": [message.get("role") for message in reflect_messages],
        "prompt_block_chars": {
            "reflect_text": len(reflect_text or ""),
            "expected_outcome_text": len(expected_outcome_text or ""),
            "verifier_signals": len(verifier_signals or ""),
            "before_summary": len(before_summary or ""),
            "after_summary": len(after_summary or ""),
            "screen_info": len(screen_info or ""),
        },
    }
    if configurable.get("trace_request_messages"):
        payload["request_messages"] = reflect_debug_messages
    if configurable.get("trace_prompt_blocks"):
        payload["prompt_blocks"] = {
            "reflect_text": reflect_text,
            "expected_outcome_text": expected_outcome_text,
            "verifier_signals": verifier_signals,
            "before_summary": before_summary,
            "after_summary": after_summary,
            "screen_info": screen_info,
        }
    if "request_messages" in payload or "prompt_blocks" in payload:
        emit_trace(config, state, "reflect", "reflect_prompt_debug", payload)


def _strip_images_for_reflect_prompt_debug(messages: list[dict]) -> list[dict]:
    return [MessageBuilder.remove_images_from_message(dict(message)) for message in messages]


def _takeover_threshold(config: RunnableConfig) -> int:
    configurable = config.get("configurable", {})
    try:
        value = int(configurable.get("verifier_takeover_threshold", DEFAULT_TAKEOVER_RETRY_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_TAKEOVER_RETRY_THRESHOLD
    return max(1, value)


def _has_positive_verifier_progress(verifier_result) -> bool:
    evidence = getattr(verifier_result, "evidence", None) or {}
    progress = evidence.get("progress_signals") if isinstance(evidence, dict) else None
    return bool(isinstance(progress, dict) and progress.get("strong_progress"))


def reflect_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Reflect node: capture screen again, ask model to judge if action succeeded.

    This is a NEW node not present in the old code. Core value of Plan-Execute-Reflect.
    """
    configurable = config.get("configurable", {})
    model_client = configurable["model_client"]
    device_factory = configurable["device_factory"]
    device_id = state.get("device_id")
    verbose = configurable.get("verbose", True)
    lang = state.get("lang", "cn")

    action_parsed = state.get("action_parsed")
    action_result = state.get("action_result")
    task = state["task"]
    task_for_prompt = str(sanitize_context_payload(task, "task", consumer="checkpoint", task_context=task))
    step_count = state["step_count"]
    max_steps = state["max_steps"]
    context_mode = get_context_mode(state, config)

    # 1. Capture screen again
    screenshot = device_factory.get_screenshot(device_id)
    current_app = device_factory.get_current_app(device_id)
    if screenshot_failure_code(screenshot):
        return _screenshot_failure_update(
            state=state,
            config=config,
            screenshot=screenshot,
            current_app=current_app,
            context_mode=context_mode,
        )
    after_observation = _build_after_observation(
        state=state,
        config=config,
        screenshot=screenshot,
        current_app=current_app,
        device_factory=device_factory,
        device_id=device_id,
    )
    after_verifier_observation = _verifier_observation_payload(
        after_observation,
        task_context=task,
    )
    before_verifier_observation = _state_before_observation_payload(
        state,
        task_context=task,
    )
    device_verifier_signals = _collect_device_verifier_signals(
        device_factory=device_factory,
        device_id=device_id,
        config=config,
    )
    if device_verifier_signals:
        after_verifier_observation = {
            **after_verifier_observation,
            "device_signals": device_verifier_signals,
        }
    verifier_result = verify_action_outcome(
        before_state={
            **state,
            "expected_outcome": state.get("expected_outcome"),
        },
        after_screenshot=screenshot,
        after_app=current_app,
        action_result=action_result,
        before_observation=before_verifier_observation,
        after_observation=after_verifier_observation,
    )
    pending_finish = bool(state.get("pending_finish")) or (
        isinstance(action_parsed, dict) and action_parsed.get("_metadata") == "finish"
    )
    task_goal_contract = ensure_task_goal_contract(state)
    finish_validation = None
    if pending_finish:
        finish_claim_message = None
        if isinstance(action_parsed, dict):
            finish_claim_message = action_parsed.get("message")
        finish_validation = validate_finish_claim(
            contract=task_goal_contract,
            verifier_status=verifier_result.status,
            verifier_evidence=verifier_result.evidence,
            after_observation=after_verifier_observation,
            finish_claim=finish_claim_message if isinstance(finish_claim_message, str) else None,
        )
    verifier_result_dict = _sanitize_verifier_result_dict(
        verifier_result,
        task_context=task,
    )
    verifier_evidence = _sanitize_verifier_evidence(
        verifier_result.evidence,
        task_context=task,
    )
    safe_before_verifier_observation = _sanitize_verifier_observation_payload(
        before_verifier_observation,
        task_context=task,
    )
    safe_after_verifier_observation = _sanitize_verifier_observation_payload(
        after_verifier_observation,
        task_context=task,
    )
    emit_trace(
        config,
        state,
        "reflect",
        "reflect_start",
        {
            "current_app": current_app,
            "action": action_parsed,
            "action_result": action_result,
            "before_observation": safe_before_verifier_observation,
            "after_observation": after_observation.mark_provider_observation,
            "device_signals": sanitize_context_payload(
                device_verifier_signals,
                consumer="checkpoint",
                task_context=task,
            ),
            "verifier_result": verifier_result_dict,
            "task_goal_contract": task_goal_trace_payload(state),
            "finish_validation": finish_validation,
        },
    )

    deterministic_reflection = None
    if pending_finish and finish_validation is not None:
        if finish_validation.get("status") == "success":
            deterministic_reflection = ReflectionResult(
                "succeeded",
                None,
                "finish",
                "finish claim validated by task goal contract",
                True,
            )
        elif finish_validation.get("status") == "failure" or task_goal_contract.goal_type != "generic_task":
            deterministic_reflection = ReflectionResult(
                "failed",
                "goal_not_satisfied",
                "continue",
                "finish claim rejected: final goal evidence missing",
                True,
            )
    else:
        deterministic_reflection = _reflection_from_verifier(verifier_result)

    # 2. Build reflection prompt with language selection
    if lang == "en":
        system_prompt = REFLECT_SYSTEM_PROMPT_EN
    else:
        system_prompt = REFLECT_SYSTEM_PROMPT_CN

    action_str = str(
        sanitize_context_payload(action_parsed, consumer="checkpoint", task_context=task)
    ) if action_parsed else "None"
    result_str = str(
        sanitize_context_payload(action_result, consumer="checkpoint", task_context=task)
    ) if action_result else "None"
    expected_outcome_text = expected_outcome_prompt_block(
        state.get("expected_outcome"),
        lang=lang,
        task_context=task,
    )
    task_goal_text = task_goal_prompt_block(
        {"task": task, "task_goal_contract": task_goal_contract.to_trace_payload()},
        lang=lang,
    )
    if deterministic_reflection is None:
        verifier_signals = str(
            _sanitize_verifier_result_dict(verifier_result, task_context=task)
        )
        before_summary = str(safe_before_verifier_observation)
        after_summary = str(safe_after_verifier_observation)
        screen_info = MessageBuilder.build_screen_info(current_app)
        if lang == "en":
            reflect_text = (
                f"Original task: {task_for_prompt}\n"
                f"Current step: {step_count} / {max_steps}\n"
                f"{task_goal_text}\n"
                f"Action just executed: {action_str}\n"
                f"Execution result: {result_str}\n"
                f"{expected_outcome_text}\n"
                f"Deterministic verifier signals: {verifier_signals}\n"
                f"Before-observation summary: {before_summary}\n"
                f"After-observation summary: {after_summary}\n"
                f"Current screen info: {screen_info}\n\n"
                f"Output JSON with action_effect, task_progress, matched_postconditions, "
                f"missing_postconditions, dynamic_change_only, evidence, next_strategy."
            )
        else:
            reflect_text = (
                f"原始任务：{task_for_prompt}\n"
                f"当前步数：{step_count} / {max_steps}\n"
                f"{task_goal_text}\n"
                f"刚执行的动作：{action_str}\n"
                f"执行结果：{result_str}\n"
                f"{expected_outcome_text}\n"
                f"确定性验证信号：{verifier_signals}\n"
                f"动作前观测摘要：{before_summary}\n"
                f"动作后观测摘要：{after_summary}\n"
                f"当前屏幕信息：{screen_info}\n\n"
                f"请输出 JSON，字段为 action_effect、task_progress、matched_postconditions、"
                f"missing_postconditions、dynamic_change_only、evidence、next_strategy。"
            )

        reflect_messages = [
            MessageBuilder.create_system_message(system_prompt),
            MessageBuilder.create_user_message(
                text=reflect_text,
                image_base64=screenshot.base64_data,
                image_mime_type=getattr(screenshot, "mime_type", "image/png"),
            ),
        ]
        _maybe_emit_reflect_prompt_debug(
            config,
            state,
            reflect_messages=reflect_messages,
            reflect_text=reflect_text,
            expected_outcome_text=expected_outcome_text,
            verifier_signals=verifier_signals,
            before_summary=before_summary,
            after_summary=after_summary,
            screen_info=screen_info,
        )

        try:
            try:
                response = model_client.request(
                    reflect_messages, output_mode="json_schema", validate_action=False
                )
            except TypeError as type_error:
                if "output_mode" not in str(type_error) and "validate_action" not in str(type_error):
                    raise
                response = model_client.request(reflect_messages)
            raw_action = response.action.strip()
            parsed_reflection = parse_reflection_action(raw_action)
            reflection = response.thinking.strip() or parsed_reflection.message
        except Exception as e:
            error_message = f"Reflection failed: {type(e).__name__}"
            if verbose:
                print(error_message)
            emit_trace(
                config,
                state,
                "reflect",
                "reflect_error",
                {
                    "message": error_message,
                    "parse_metadata": getattr(e, "parse_metadata", {}) or {},
                },
            )
            raw_action = ""
            parsed_reflection = ReflectionResult("failed", "model_reflection_failed", "retry", error_message)
            reflection = error_message
    else:
        raw_action = json.dumps(
            {
                "verdict": deterministic_reflection.verdict,
                "failure_cause": deterministic_reflection.failure_cause or "none",
                "suggested_strategy": deterministic_reflection.suggested_strategy or "continue",
                "message": deterministic_reflection.message,
            },
            ensure_ascii=False,
        )
        parsed_reflection = deterministic_reflection
        reflection = deterministic_reflection.message

    # 4. Parse reflection
    reflection_state_value = _redacted_private_text(str(reflection or ""))
    action_succeeded = parsed_reflection.verdict == "succeeded"
    task_finished = parsed_reflection.suggested_strategy == "finish"

    reflection_fields = merge_verifier_with_reflection(
        verifier_result,
        {
            "action_succeeded": action_succeeded,
            "reflection_verdict": parsed_reflection.verdict,
            "failure_cause": parsed_reflection.failure_cause,
            "reflection_has_evidence": parsed_reflection.has_evidence,
        },
    )
    action_succeeded = bool(reflection_fields["action_succeeded"])
    final_verdict = reflection_fields["reflection_verdict"]
    final_failure_cause = reflection_fields.get("failure_cause")
    retry_count = int(state.get("retry_count") or 0)
    has_positive_progress = _has_positive_verifier_progress(verifier_result)
    if final_verdict in {"failed", "partial"}:
        if has_positive_progress:
            retry_count = 0
        else:
            retry_count += 1
    takeover_update = {}
    if (
        final_verdict in {"failed", "partial"}
        and retry_count >= _takeover_threshold(config)
        and not task_finished
        and not has_positive_progress
    ):
        parsed_reflection.suggested_strategy = "takeover"
        takeover_update = {
            "pending_interrupt": "takeover",
            "interrupt_message": "Repeated verification failures require human takeover",
            "hitl_count": int(state.get("hitl_count") or 0) + 1,
        }
    if verifier_result.hard_failure or final_verdict != "succeeded":
        task_finished = False
    if (
        pending_finish
        and finish_validation is not None
        and finish_validation.get("status") == "unknown"
        and task_goal_contract.goal_type == "generic_task"
        and task_finished
        and parsed_reflection.has_evidence
        and final_verdict == "succeeded"
    ):
        finish_validation = {
            **finish_validation,
            "status": "success",
            "matched_terminal_evidence": list(finish_validation.get("matched_terminal_evidence") or [])
            + ["model_reflection_evidence"],
            "missing_terminal_evidence": [],
        }
    if pending_finish and finish_validation is not None and finish_validation.get("status") != "success":
        task_finished = False
        final_verdict = "failed"
        final_failure_cause = "goal_not_satisfied"
        parsed_reflection.suggested_strategy = "continue"

    context_updates = {"context_mode": context_mode}
    if context_enabled(context_mode):
        loading = parsed_reflection.failure_cause in {
            "network_or_loading", "app_not_responding",
        }
        sensitive = parsed_reflection.failure_cause in {
            "unsafe_or_sensitive", "permission_or_login_or_captcha",
        }
        if parsed_reflection.verdict == "succeeded":
            confidence = "high"
        elif parsed_reflection.verdict == "partial":
            confidence = "medium"
        else:
            confidence = "low"
        belief = build_screen_belief(
            current_app=current_app,
            step_count=step_count,
            summary=(
                f"verdict={final_verdict} "
                f"cause={final_failure_cause or 'none'} "
                f"strategy={parsed_reflection.suggested_strategy or 'none'}"
            ),
            loading_or_blocked=loading,
            unsafe_or_sensitive=sensitive,
            confidence=confidence,
        )
        outcome_state = {
            **state,
            "current_app": current_app,
            "reflection_verdict": final_verdict,
            "failure_cause": final_failure_cause,
            "suggested_strategy": parsed_reflection.suggested_strategy,
        }
        outcome = build_action_outcome_summary(outcome_state)
        existing_failure_memory = list(state.get("failure_memory") or [])
        repeated = detect_repeated_failure(existing_failure_memory, outcome)
        failure_memory = update_failure_memory(
            existing_failure_memory, outcome, state.get("context_budget")
        )
        summarized_history, history_truncated = update_summarized_history(
            str(state.get("summarized_history") or ""),
            outcome,
            state.get("context_budget"),
        )
        context_updates = {
            "context_mode": context_mode,
            "screen_belief": belief,
            "action_outcome_summary": outcome,
            "failure_memory": failure_memory,
            "summarized_history": summarized_history,
            "short_term_memory": {
                "screen_belief": belief,
                "last_action_outcome": outcome,
                "latest_failures": failure_memory[-3:],
                "grounding_observation": state.get("grounding_observation"),
            },
            "action_ledger": (list(state.get("action_ledger") or []) + [outcome])[-10:],
            "context_truncated": bool(state.get("context_truncated")) or history_truncated,
            "failure_memory_hit_count": int(state.get("failure_memory_hit_count") or 0)
            + (1 if repeated else 0),
            "repeated_failure_count": int(state.get("repeated_failure_count") or 0)
            + (1 if repeated else 0),
        }
        context_updates["gui_memory"] = update_gui_memory(
            {**state, **context_updates, "action_result": action_result},
            current_app=current_app,
            screen_id=state.get("screen_id"),
        )

    emit_trace(
        config,
        state,
        "reflect",
        "reflect_result",
        {
            "reflection": reflection,
            "action_raw": raw_action,
            "reflection_verdict": final_verdict,
            "failure_cause": final_failure_cause,
            "suggested_strategy": parsed_reflection.suggested_strategy,
            "action_succeeded": action_succeeded,
            "finished": task_finished,
            "verifier_result": verifier_result_dict,
            "verifier_status": verifier_result.status,
            "verifier_failure_cause": verifier_result.failure_cause,
            "verifier_evidence": verifier_evidence,
            "task_goal_contract": task_goal_trace_payload(state),
            "pending_finish": pending_finish,
            "finish_validation_status": finish_validation.get("status") if finish_validation else None,
            "finish_validation_evidence": finish_validation,
            "context_mode": context_mode,
            "context_truncated": context_updates.get("context_truncated", False),
            "failure_memory_hit_count": context_updates.get("failure_memory_hit_count", 0),
            "repeated_failure_count": context_updates.get("repeated_failure_count", 0),
        },
    )

    return {
        "screenshot_b64": screenshot.base64_data,
        "current_app": current_app,
        "screen_id": after_observation.snapshot.screen_id,
        "screen_hash": after_observation.snapshot.screen_hash,
        "observation": after_observation.to_dict(),
        "mark_registry": after_observation.mark_registry.to_dict(),
        "reflection": reflection_state_value,
        "action_succeeded": action_succeeded,
        "reflection_verdict": final_verdict,
        "failure_cause": final_failure_cause,
        "suggested_strategy": parsed_reflection.suggested_strategy,
        "verifier_result": verifier_result_dict,
        "verifier_status": verifier_result.status,
        "verifier_failure_cause": verifier_result.failure_cause,
        "verifier_evidence": verifier_evidence,
        "pending_finish": False,
        "finish_validation_status": finish_validation.get("status") if finish_validation else None,
        "finish_validation_evidence": finish_validation,
        "task_goal_contract": task_goal_trace_payload(state),
        "retry_count": retry_count,
        "finished": task_finished,
        **takeover_update,
        **context_updates,
    }
