"""Reflect node: screenshot → structured action outcome reflection."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.device_factory import ObservationCaptureError
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY, VerificationPolicy
from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    build_action_outcome_summary,
    build_screen_belief,
    context_enabled,
    detect_repeated_action,
    detect_repeated_failure,
    get_context_mode,
    normalize_failure_cause,
    sanitize_context_payload,
    select_reflect_context,
    _redacted_private_text,
    update_gui_memory,
    update_failure_memory,
    update_summarized_history,
)
from phone_agent.graph.compatibility_adapters import observe_legacy_page_signals
from phone_agent.graph.expected_outcome import expected_outcome_prompt_block
from phone_agent.graph.device_observation import capture_device_observation
from phone_agent.graph.nodes.observation_capture import (
    build_after_observation,
    collect_device_verifier_signals,
    sanitize_verifier_observation_payload,
    screenshot_failure_update,
    state_before_observation_payload,
    observation_shape_diff,
    verifier_observation_payload,
)
from phone_agent.graph.screenshot_status import screenshot_failure_code
from phone_agent.graph.goal import (
    build_goal_prompt_block,
    goal_trace_payload,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.graph.verifier import (
    merge_verifier_with_reflection,
    verify_action_outcome,
)
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效，并给出下一步建议。

你必须只输出一个 JSON 对象，不要 Markdown、XML、函数调用或多余文本：
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id 或屏幕上的具体元素","observed_value":"你在该处实际看到的文字"}]}

判断标准：
1. 动作生效：页面满足预期后置条件（如输入框聚焦、目标文本出现、目标页面打开、目标应用打开）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"任务已完成","named_evidence":[{"criterion":"成功标准名","screen_reference":"屏幕证据引用"}]}

重要约束：
- named_evidence 仅在 suggested_strategy="finish" 时需要输出，且只列出契约中标记为 [judge] 的成功标准。标记为 [auto] 的标准由系统读取设备状态自行核验，你不需要点名或回报。
- 每条证据给出：criterion（标准名）、screen_reference（mark_id 或屏幕上的具体元素，不要写"区域1"/"屏幕"这类占位）、observed_value（你在该处实际看到的原文）。照实回报你看到的文字即可，不要猜测系统内部使用的取值。observed_value 仅用于当前 node 匹配，不写入 state/trace。
- 只有在截图明确显示加载中、空白页、网络错误、进度条/转圈、或执行结果表示应用无响应时，才使用 failure_cause="network_or_loading" 和 suggested_strategy="wait"。
- 如果刚执行的是 Launch/启动应用，且当前屏幕信息或截图已显示目标应用/设置页/目标页面已打开，即使任务还没完成，也应判定为 succeeded + continue，而不是 partial + wait。
- 不要因为页面内容很多、设置项列表尚需下一步操作，就误判为加载中；可继续操作的稳定页面应输出 continue。
- 广告、banner、推荐流、热词、计数器或首页动态内容变化只能作为噪声，不能单独证明 Tap/Type/搜索/打开视频成功；必须引用后置条件证据。
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded, then give next-step advice.

You MUST output exactly one JSON object. Do not output Markdown, XML, function calls, or extra text:
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id or a concrete on-screen element","observed_value":"the text you actually see there"}]}

Judgment criteria:
1. Action succeeded: expected postconditions are satisfied (for example focused input, expected text, target page, or target app)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed","named_evidence":[{"criterion":"criterion_name","screen_reference":"screen evidence reference"}]}

Important constraints:
- named_evidence is only required when suggested_strategy="finish", and only for criteria marked [judge] in the contract. Criteria marked [auto] are verified by the system from device state — do not cite or report them.
- For each evidence item give: criterion (its name), screen_reference (a mark_id or concrete on-screen element — never a placeholder like "region-1"/"screen"), and observed_value (the text you actually see there). Report what you see verbatim; do not guess values the system uses internally. observed_value is node-local and must not enter state or trace.
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
VERIFIED_REFLECTION_SKIP_CONFIDENCE = DEFAULT_VERIFICATION_POLICY.value(
    "verified_reflection_skip_confidence"
)
DEFAULT_TAKEOVER_RETRY_THRESHOLD = int(
    DEFAULT_VERIFICATION_POLICY.value("takeover_retry_count")
)


@dataclass
class ReflectionResult:
    verdict: str
    failure_cause: str | None
    suggested_strategy: str | None
    message: str
    has_evidence: bool = False
    named_evidence: list[dict[str, object]] | None = None


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
    if (
        verifier_result.status == "success"
        and verifier_result.confidence >= VERIFIED_REFLECTION_SKIP_CONFIDENCE
    ):
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
        if (
            action_effect in {"succeeded", "success"}
            and not dynamic_only
            and not has_missing
            and has_positive_evidence
        ):
            verdict = "succeeded"
            failure_cause = "none"
            suggested_strategy = str(data.get("next_strategy", "continue"))
        elif action_effect in {"succeeded", "success"} and (
            dynamic_only or has_missing
        ):
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
    parsed_cause = (
        None if verdict == "succeeded" and failure_cause == "none" else failure_cause
    )
    named_evidence_raw = data.get("named_evidence") if isinstance(data, dict) else None
    named_evidence = None
    if isinstance(named_evidence_raw, list):
        named_evidence = []
        for item in named_evidence_raw:
            if not isinstance(item, dict):
                continue
            evidence_item: dict[str, object] = {
                "criterion": str(item.get("criterion", ""))[:128],
                "screen_reference": str(item.get("screen_reference", ""))[:128],
            }
            if "observed_value" in item:
                evidence_item["observed_value"] = item.get("observed_value")
            if item.get("source") in {
                "accessibility",
                "screen_object",
                "mark",
                "visual_region",
                "whole_screen",
                "external_probe",
                "device",
            }:
                evidence_item["source"] = item.get("source")
            named_evidence.append(evidence_item)
    if named_evidence:
        has_evidence = True
    return ReflectionResult(
        verdict, parsed_cause, suggested_strategy, message, has_evidence, named_evidence
    )
















SAFE_VERIFIER_EVIDENCE_STRINGS = {
    "after_observation_unavailable",
    "app_opened",
    "content_shift_unverified",
    "focused_editable_or_keyboard_visible",
    "input_focused",
    "input_progress",
    "postcondition_unverified",
    "typed_text_present",
}


def _sanitize_verifier_evidence(
    evidence: dict, *, task_context: str | None = None
) -> dict:
    """Sanitize verifier evidence while preserving stable machine codes."""

    safe = sanitize_context_payload(
        evidence, consumer="trace_payload", task_context=task_context
    )
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
    if (
        item in SAFE_VERIFIER_EVIDENCE_STRINGS
        or item.startswith("sha256:")
        or item.startswith("forbidden:sha256:")
    ):
        return item
    return _redacted_private_text(item)


def _sanitize_verifier_result_dict(
    verifier_result, *, task_context: str | None = None
) -> dict:
    data = verifier_result.to_dict()
    if isinstance(data.get("evidence"), dict):
        data["evidence"] = _sanitize_verifier_evidence(
            data["evidence"], task_context=task_context
        )
    if isinstance(data.get("signals"), dict):
        data["signals"] = sanitize_context_payload(
            data["signals"], consumer="reflect_prompt", task_context=task_context
        )
    return data


def _maybe_emit_reflect_prompt_debug(
    config: RunnableConfig,
    state: "AgentState",
    *,
    reflect_messages: list[dict],
    reflect_text: str,
    expected_outcome_text: str,
    verifier_signals: str,
    observation_diff: str,
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
            "observation_diff": len(observation_diff or ""),
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
            "observation_diff": observation_diff,
            "screen_info": screen_info,
        }
    if "request_messages" in payload or "prompt_blocks" in payload:
        emit_trace(config, state, "reflect", "reflect_prompt_debug", payload)


def _strip_images_for_reflect_prompt_debug(messages: list[dict]) -> list[dict]:
    return [
        MessageBuilder.remove_images_from_message(dict(message)) for message in messages
    ]


def _takeover_threshold(config: RunnableConfig) -> int:
    configurable = config.get("configurable", {})
    policy = configurable.get("verification_policy")
    if isinstance(policy, VerificationPolicy):
        return max(1, int(policy.value("takeover_retry_count")))
    try:
        value = int(
            configurable.get(
                "verifier_takeover_threshold", DEFAULT_TAKEOVER_RETRY_THRESHOLD
            )
        )
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
    task_for_prompt = str(
        sanitize_context_payload(task, "task", consumer="reflect_prompt", task_context=task)
    )
    step_count = state["step_count"]
    max_steps = state["max_steps"]
    context_mode = get_context_mode(state, config)

    # 1. Capture screen again
    try:
        device_capture = capture_device_observation(
            device_factory,
            device_id,
            timeout=int(configurable.get("screenshot_timeout", 10) or 10),
            max_attempts=int(configurable.get("observation_capture_attempts", 2) or 2),
        )
    except ObservationCaptureError as exc:
        return {
            "reflection": f"Observation unavailable: {exc.code}",
            "action_succeeded": False,
            "reflection_verdict": "retry",
            "failure_cause": "context_lost",
            "suggested_strategy": "wait",
            "grounding_error": exc.code,
            "grounding_failure_code": exc.code,
            "retry_count": int(state.get("retry_count") or 0) + 1,
            "finished": False,
        }
    screenshot = device_capture.screenshot
    current_app = device_capture.current_app
    if screenshot_failure_code(screenshot):
        return screenshot_failure_update(
            state=state,
            config=config,
            screenshot=screenshot,
            current_app=current_app,
            context_mode=context_mode,
        )
    after_observation = build_after_observation(
        state=state,
        config=config,
        screenshot=screenshot,
        current_app=current_app,
        foreground=device_capture.foreground,
        observation_epoch=device_capture.observation_epoch,
        device_factory=device_factory,
        device_id=device_id,
    )
    after_verifier_observation = verifier_observation_payload(
        after_observation,
        task_context=task,
    )
    before_verifier_observation = state_before_observation_payload(
        state,
        task_context=task,
    )
    device_verifier_signals = collect_device_verifier_signals(
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
        page_signal_adapter=None,
    )
    if configurable.get("enable_legacy_page_signal_adapter", False):
        observe_legacy_page_signals(
            expected=state.get("expected_outcome"),
            observation=after_verifier_observation,
        )
    verifier_result_dict = _sanitize_verifier_result_dict(
        verifier_result,
        task_context=task,
    )
    verifier_evidence = _sanitize_verifier_evidence(
        verifier_result.evidence,
        task_context=task,
    )
    safe_before_verifier_observation = sanitize_verifier_observation_payload(
        before_verifier_observation,
        task_context=task,
    )
    safe_after_verifier_observation = sanitize_verifier_observation_payload(
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
                consumer="reflect_prompt",
                task_context=task,
            ),
            "verifier_result": verifier_result_dict,
            "goal_contract": goal_trace_payload(state, config),
        },
    )

    deterministic_reflection = _reflection_from_verifier(verifier_result)

    # 2. Build reflection prompt with language selection
    if lang == "en":
        system_prompt = REFLECT_SYSTEM_PROMPT_EN
    else:
        system_prompt = REFLECT_SYSTEM_PROMPT_CN

    action_str = (
        str(
            sanitize_context_payload(
                action_parsed, consumer="reflect_prompt", task_context=task
            )
        )
        if action_parsed
        else "None"
    )
    result_str = (
        str(
            sanitize_context_payload(
                action_result, consumer="reflect_prompt", task_context=task
            )
        )
        if action_result
        else "None"
    )
    expected_outcome_text = expected_outcome_prompt_block(
        state.get("expected_outcome"),
        lang=lang,
        task_context=task,
    )
    goal_contract_block = build_goal_prompt_block(state, lang=lang, config=config)
    reflect_context_selection = select_reflect_context(
        state,
        mode=context_mode,
        lang=lang,
        prompt_version=configurable.get("prompt_version"),
    )
    reflect_context_block = reflect_context_selection.context_block
    if deterministic_reflection is None:
        verifier_signals = str(
            _sanitize_verifier_result_dict(verifier_result, task_context=task)
        )
        observation_diff = str(
            observation_shape_diff(
                safe_before_verifier_observation,
                safe_after_verifier_observation,
            )
        )
        screen_info = MessageBuilder.build_screen_info(current_app)
        # dynamic user prompt: includes task context, action, results, observations
        # goal_contract block is injected as a separate user message before this one
        # to enable prompt prefix caching
        if lang == "en":
            reflect_text = (
                f"Original task: {task_for_prompt}\n"
                f"Current step: {step_count} / {max_steps}\n"
                f"Action just executed: {action_str}\n"
                f"Execution result: {result_str}\n"
                f"{expected_outcome_text}\n"
                f"Deterministic verifier signals: {verifier_signals}\n"
                f"Before/after observation shape diff: {observation_diff}\n"
                f"Current screen info: {screen_info}\n\n"
            )
            if reflect_context_block:
                reflect_text = f"{reflect_text}\n{reflect_context_block}\n\n"
            reflect_text = (
                f"{reflect_text}"
                f"Output JSON with action_effect, task_progress, matched_postconditions, "
                f"missing_postconditions, dynamic_change_only, evidence, next_strategy, named_evidence."
            )
        else:
            reflect_text = (
                f"原始任务：{task_for_prompt}\n"
                f"当前步数：{step_count} / {max_steps}\n"
                f"刚执行的动作：{action_str}\n"
                f"执行结果：{result_str}\n"
                f"{expected_outcome_text}\n"
                f"确定性验证信号：{verifier_signals}\n"
                f"动作前后观测形状差异：{observation_diff}\n"
                f"当前屏幕信息：{screen_info}\n\n"
            )
            if reflect_context_block:
                reflect_text = f"{reflect_text}\n{reflect_context_block}\n\n"
            reflect_text = (
                f"{reflect_text}"
                f"请输出 JSON，字段为 action_effect、task_progress、matched_postconditions、"
                f"missing_postconditions、dynamic_change_only、evidence、next_strategy、named_evidence。"
            )

        # Build messages: system(static) + goal_contract_block(static task-wide, separate msg for cache) + dynamic user
        reflect_messages = [
            MessageBuilder.create_system_message(system_prompt),
        ]
        if goal_contract_block:
            reflect_messages.append(
                MessageBuilder.create_user_message(text=goal_contract_block)
            )
        reflect_messages.append(
            MessageBuilder.create_user_message(
                text=reflect_text,
                image_base64=screenshot.base64_data,
                image_mime_type=getattr(screenshot, "mime_type", "image/png"),
            )
        )
        _maybe_emit_reflect_prompt_debug(
            config,
            state,
            reflect_messages=reflect_messages,
            reflect_text=reflect_text,
            expected_outcome_text=expected_outcome_text,
            verifier_signals=verifier_signals,
            observation_diff=observation_diff,
            screen_info=screen_info,
        )

        try:
            try:
                response = model_client.request(
                    reflect_messages, output_mode="json_schema", validate_action=False
                )
            except TypeError as type_error:
                if "output_mode" not in str(
                    type_error
                ) and "validate_action" not in str(type_error):
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
            parsed_reflection = ReflectionResult(
                "failed", "model_reflection_failed", "retry", error_message
            )
            reflection = error_message
    else:
        raw_action = json.dumps(
            {
                "verdict": deterministic_reflection.verdict,
                "failure_cause": deterministic_reflection.failure_cause or "none",
                "suggested_strategy": deterministic_reflection.suggested_strategy
                or "continue",
                "message": deterministic_reflection.message,
            },
            ensure_ascii=False,
        )
        parsed_reflection = deterministic_reflection
        reflection = deterministic_reflection.message

    # 4. Parse reflection
    # Keep the reflection readable in state: plan injects it into the next prompt, and
    # a `{redacted, length}` stub there told the model nothing while costing a block.
    # That stub is the checkpoint-consumer policy; privacy for this field is enforced
    # at trace and checkpoint egress, which both classify `reflection` as private.
    reflection_state_value = sanitize_context_payload(
        str(reflection or ""),
        "reflection",
        consumer="inject",
        task_context=task if isinstance(task, str) else None,
    )
    action_succeeded = parsed_reflection.verdict == "succeeded"

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
        and not has_positive_progress
    ):
        parsed_reflection.suggested_strategy = "takeover"
        takeover_update = {
            "pending_interrupt": "takeover",
            "interrupt_message": "Repeated verification failures require human takeover",
            "hitl_count": int(state.get("hitl_count") or 0) + 1,
        }
    # Reflect judges one action and can never finish the task: only a finish
    # claim routed to the acceptance node can do that. A model that suggests
    # "finish" here is asking to emit a finish action next, not declaring
    # completion, so the suggestion is downgraded to keep planning.
    if parsed_reflection.suggested_strategy == "finish" and (
        verifier_result.hard_failure or final_verdict != "succeeded"
    ):
        parsed_reflection.suggested_strategy = "continue"

    context_updates = {"context_mode": context_mode}
    if context_enabled(context_mode):
        loading = parsed_reflection.failure_cause in {
            "network_or_loading",
            "app_not_responding",
        }
        sensitive = parsed_reflection.failure_cause in {
            "unsafe_or_sensitive",
            "permission_or_login_or_captcha",
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
        outcome["action_receipt"] = state.get("action_receipt")
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
            "context_truncated": bool(state.get("context_truncated"))
            or history_truncated,
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
        # Trajectory-level check, deliberately separate from `detect_repeated_failure`:
        # a loop where every step verifies as successful is invisible to failure memory,
        # so re-using one target on one surface is judged on its own terms here.
        tried_actions = list(context_updates["gui_memory"].get("tried_actions") or [])
        context_updates["repeated_action_detected"] = bool(
            tried_actions
            and detect_repeated_action(tried_actions[:-1], tried_actions[-1])
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
            "verifier_result": verifier_result_dict,
            "verifier_status": verifier_result.status,
            "verifier_failure_cause": verifier_result.failure_cause,
            "verifier_evidence": verifier_evidence,
            "goal_contract": goal_trace_payload(state, config),
            "context_mode": context_mode,
            "context_truncated": context_updates.get("context_truncated", False),
            "failure_memory_hit_count": context_updates.get(
                "failure_memory_hit_count", 0
            ),
            "repeated_failure_count": context_updates.get("repeated_failure_count", 0),
        },
    )

    return {
        "screenshot_b64": None,
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
        "retry_count": retry_count,
        # Reflect judges a single action and never completes the task; only the
        # acceptance node can set this True, after the goal gate passes.
        "finished": False,
        **takeover_update,
        **context_updates,
    }
