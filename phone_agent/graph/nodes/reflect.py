"""Reflect node: screenshot → structured action outcome reflection."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from phone_agent.device_factory import ObservationCaptureError
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY
from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    build_action_outcome_summary,
    build_screen_belief,
    context_enabled,
    detect_repeated_action,
    detect_repeated_failure,
    failure_memory_write_mode,
    get_context_mode,
    normalize_failure_cause,
    sanitize_context_payload,
    repeated_action_key,
    select_reflect_context,
    trajectory_liveness,
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
    ensure_goal_contract,
    goal_runtime_reference,
    goal_trace_payload,
)
from phone_agent.graph import goal_evidence
from phone_agent.graph.fact_providers import collect_goal_facts
from phone_agent.graph.trace import emit_trace
from phone_agent.graph.verifier import (
    merge_verifier_with_reflection,
    verify_action_outcome,
)
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效。

你必须只输出一个 JSON 对象，不要 Markdown、XML、函数调用或多余文本：
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id 或屏幕上的具体元素","observed_value":"你在该处实际看到的文字"}]}

判断标准：
1. 动作生效：页面满足预期后置条件（如输入框聚焦、目标文本出现、目标页面打开、目标应用打开）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"任务已完成","named_evidence":[{"criterion":"成功标准名","screen_reference":"屏幕证据引用"}]}

重要约束：
- message 只描述当前屏幕观察到的客观状态；禁止行动指令、禁止目标名/输入内容建议。
- named_evidence 仅在 suggested_strategy="finish" 时需要输出，且只列出契约中标记为 [judge] 的成功标准。标记为 [auto] 的标准由系统读取设备状态自行核验，你不需要点名或回报。
- 每条证据给出：criterion（标准名）、screen_reference（mark_id 或屏幕上的具体元素，不要写"区域1"/"屏幕"这类占位）、observed_value（你在该处实际看到的原文）。照实回报你看到的文字即可，不要猜测系统内部使用的取值。observed_value 仅用于当前 node 匹配，不写入 state/trace。
- 只有在截图明确显示加载中、空白页、网络错误、进度条/转圈、或执行结果表示应用无响应时，才使用 failure_cause="network_or_loading" 和 suggested_strategy="wait"。
- 如果刚执行的是 Launch/启动应用，且当前屏幕信息或截图已显示目标应用/设置页/目标页面已打开，即使任务还没完成，也应判定为 succeeded + continue，而不是 partial + wait。
- 不要因为页面内容很多、设置项列表尚需下一步操作，就误判为加载中；可继续操作的稳定页面应输出 continue。
- 广告、banner、推荐流、热词、计数器或首页动态内容变化只能作为噪声，不能单独证明 Tap/Type/搜索/打开视频成功；必须引用后置条件证据。
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded.

You MUST output exactly one JSON object. Do not output Markdown, XML, function calls, or extra text:
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id or a concrete on-screen element","observed_value":"the text you actually see there"}]}

Judgment criteria:
1. Action succeeded: expected postconditions are satisfied (for example focused input, expected text, target page, or target app)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed","named_evidence":[{"criterion":"criterion_name","screen_reference":"screen evidence reference"}]}

Important constraints:
- message describes only the objective state observed on the current screen; no action instructions, no target names, and no input-content suggestions.
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

# Directive-fuse markers: reflection `message` is observation-only; if the
# model still emits an imperative/suggestion sentence, blank it and flag the
# event. This is a fuse, not the primary defense (the prompt role already
# forbids action instructions); false positives are acceptable.
_DIRECTIVE_CN_MARKERS = (
    "可以输入",
    "请点击",
    "请搜索",
    "请进入",
    "建议你",
    "你可以",
    "应该输入",
    "应该点击",
)
_DIRECTIVE_EN_MARKERS = (
    "should type",
    "should tap",
    "should search",
    "please type",
    "please tap",
    "you can type",
)


def _message_contains_directive(message: str) -> bool:
    lowered = message.lower()
    return any(marker in message for marker in _DIRECTIVE_CN_MARKERS) or any(
        marker in lowered for marker in _DIRECTIVE_EN_MARKERS
    )


def _newly_invalidated_locate_marks(
    state: dict[str, Any],
    *,
    verdict: str,
    failure_cause: str | None,
) -> list[str]:
    """S4: invalidate a tapped ``locate_*`` mark whose tap clearly did not land.

    The empirical failure mode is one wrong LA box tapped repeatedly (burning
    the locate budget). Only ``locate_*`` marks are invalidated — LA boxes may
    be wrong; accessibility-origin marks are structural and never invalidated.

    Rules (deliberately narrow; ``partial`` alone is not enough):
    - verdict ``failed`` → the action did not take effect; the box is suspect.
    - verdict ``partial`` with ``coordinate_or_tap_offset`` → the page changed
      but the tap clearly landed at the wrong place; the box is suspect.
    - anything else (including disputed partial / succeeded) → keep the mark.
    """

    action = state.get("action_parsed") or {}
    if str(action.get("action") or "") not in {"Tap", "Double Tap", "Long Press"}:
        return []
    grounding_observation = state.get("grounding_observation") or {}
    if not isinstance(grounding_observation, dict):
        return []
    target = grounding_observation.get("target") or {}
    if not isinstance(target, dict):
        return []
    mark_id = str(target.get("mark_id") or "")
    if not mark_id.startswith("locate_"):
        return []
    if verdict == "failed":
        return [mark_id]
    if verdict == "partial" and failure_cause == "coordinate_or_tap_offset":
        return [mark_id]
    return []


@dataclass
class ReflectionResult:
    verdict: str
    failure_cause: str | None
    suggested_strategy: str | None
    message: str
    has_evidence: bool = False
    named_evidence: list[dict[str, object]] | None = None
    directive_filtered: bool = False
    # P5 #1: set when the model call was skipped (deterministic path). The
    # reason distinguishes hard_failure from verifier high-confidence success
    # so the reflect_result trace can explain why no model call happened.
    model_skipped: bool = False
    model_skip_reason: str | None = None


def _judge_evidence_pending(goal_agenda: list[dict] | None) -> bool:
    """Whether any goal-contract vlm_judge criterion still awaits evidence.

    vlm_judge criteria are settled by model observation (named_evidence at
    finish), so the deterministic reflect skip must not fire while such a
    criterion is still unsatisfied — the model still needs to observe the
    screen to collect that evidence. The agenda here is the reflect-side
    fold of the same acceptance ledger (P5 #1 precondition), where a vlm_judge
    criterion counts as satisfied only once its evidence was collected at a
    trusted target-app observation and latched (P2).
    """

    for item in goal_agenda or []:
        if str(item.get("verification") or "") == "vlm_judge" and str(
            item.get("status") or ""
        ) != "satisfied":
            return True
    return False


# Stable machine codes only: the skip reason and message are written into the
# reflect_result trace and the state ``reflection`` field respectively, so they
# must never embed raw on-screen text (P0 #10 privacy at trace/checkpoint egress).
_SKIP_SAFE_POSTCONDITION_CODES = frozenset(
    {
        "app_opened",
        "surface_changed",
        "typed_text_present",
        "input_focused",
        "focused_editable_or_keyboard_visible",
        "selected_object_match",
    }
)


def _reflection_from_verifier(
    verifier_result,
    *,
    action: dict | None,
    liveness: dict[str, object],
    goal_agenda: list[dict] | None = None,
    allow_skip: bool = True,
) -> ReflectionResult | None:
    """Skip model reflection for self-evident action questions (P5 #1).

    The verifier's deterministic success signals answer "did this action take
    effect?" without a vision-language call. The skip is gated by:
    1. ``hard_failure`` never skips — it produces the deterministic failure.
    2. A pending vlm_judge criterion (``_judge_evidence_pending``) forces the
       model call so finish evidence can still be collected.
    3. status=success with confidence >= 0.9 (app_opened / surface_changed /
       typed_text_present / input_focused / text_present), or an explicit
       same-page ``selected_object_match`` (0.75 confidence but gated by P3 #4
       cross-page degradation, which already suppresses that signal across a
       page rebuild — so a match here is same-page evidence).
    4. Trajectory not stuck — a stuck run keeps the model in the loop.
    """

    if verifier_result.hard_failure:
        return ReflectionResult(
            "failed",
            verifier_result.failure_cause or "unknown",
            "retry",
            "deterministic verifier hard failure",
            True,
            model_skipped=True,
            model_skip_reason="hard_failure",
        )
    if not allow_skip:
        return None
    if liveness.get("state") == "stuck":
        return None
    if _judge_evidence_pending(goal_agenda):
        return None
    matched_postconditions = list(
        (verifier_result.evidence or {}).get("matched_postconditions") or []
    )
    high_confidence = verifier_result.status == "success" and float(
        verifier_result.confidence or 0.0
    ) >= 0.9
    same_page_selected_object = (
        verifier_result.status == "success"
        and "selected_object_match" in matched_postconditions
    )
    if not (high_confidence or same_page_selected_object):
        return None
    progress_signals = (
        (verifier_result.evidence or {}).get("progress_signals") or {}
    )
    safe_codes = [
        code
        for code in matched_postconditions
        if code in _SKIP_SAFE_POSTCONDITION_CODES
    ]
    safe_codes += [
        code
        for code in _SKIP_SAFE_POSTCONDITION_CODES
        if progress_signals.get(code) is True and code not in safe_codes
    ]
    matched_text = ", ".join(safe_codes) or "verifier success"
    return ReflectionResult(
        "succeeded",
        None,
        "continue",
        "deterministic postconditions matched",
        True,
        model_skipped=True,
        model_skip_reason=(
            f"verifier_high_confidence status=success "
            f"confidence={verifier_result.confidence:.2f} matched={matched_text}"
        ),
    )


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
    directive_filtered = _message_contains_directive(message)
    if directive_filtered:
        message = ""
    return ReflectionResult(
        verdict,
        parsed_cause,
        suggested_strategy,
        message,
        has_evidence,
        named_evidence,
        directive_filtered=directive_filtered,
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
    evidence: dict,
    *,
    task_context: str | None = None,
    consumer: str = "trace_payload",
) -> dict:
    """Sanitize verifier evidence while preserving stable machine codes."""

    safe = sanitize_context_payload(evidence, consumer=consumer, task_context=task_context)
    if not isinstance(safe, dict):
        return {}
    for key in ("matched_postconditions", "missing_postconditions"):
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if isinstance(value, list):
            safe[key] = (
                list(value)
                if consumer == "reflect_prompt"
                else [_sanitize_postcondition_item(item) for item in value]
            )
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
    verifier_result,
    *,
    task_context: str | None = None,
    consumer: str = "trace_payload",
) -> dict:
    data = verifier_result.to_dict()
    if isinstance(data.get("evidence"), dict):
        data["evidence"] = _sanitize_verifier_evidence(
            data["evidence"], task_context=task_context, consumer=consumer
        )
    if isinstance(data.get("signals"), dict):
        data["signals"] = sanitize_context_payload(
            data["signals"], consumer=consumer, task_context=task_context
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
            "observation_retry_count": int(state.get("observation_retry_count") or 0) + 1,
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
    runtime_contract_id = goal_runtime_reference(state)
    ledger = list(state.get("goal_evidence_ledger") or [])
    goal_contract = ensure_goal_contract(state, config)
    if goal_contract is not None:
        facts = collect_goal_facts(
            goal_contract=goal_contract,
            configurable=configurable,
            screenshot=screenshot,
            after_observation=after_observation,
            runtime_contract_id=runtime_contract_id,
        )
        if facts:
            in_target_app = goal_evidence.target_app_entered(
                goal_contract,
                facts["collected"],
                current_app=current_app,
                foreground_activity=after_observation.snapshot.foreground_activity,
            )
            ledger = goal_evidence.append_evaluation_entries(
                ledger,
                evaluation={"evidence": {"per_criterion": facts["collected"]}},
                contract_id=runtime_contract_id,
                screen_id=after_observation.snapshot.screen_id,
                observation_epoch=after_observation.snapshot.observation_epoch,
                predicate_ids=facts["predicate_ids"],
                target_app_entered=in_target_app,
            )
    goal_agenda: list[dict] = []
    if goal_contract is not None:
        from phone_agent.graph.goal_evaluator import pure_goal_evaluator

        programmatic_names = [
            criterion.name
            for criterion in goal_contract.success_criteria
            if criterion.verification != "vlm_judge" and criterion.predicate is not None
        ]
        folded = pure_goal_evaluator.evaluate(
            contract=goal_contract,
            contract_id=runtime_contract_id,
            evidence_ledger=ledger,
            finish_claim_matched=programmatic_names,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
        )
        per_criterion = folded.evidence.get("per_criterion") or {}
        for criterion in goal_contract.success_criteria:
            raw_status = str(
                (per_criterion.get(criterion.name) or {}).get("status") or "unknown"
            )
            status = "satisfied" if raw_status == "matched" else raw_status
            item: dict = {
                "description": sanitize_context_payload(
                    criterion.description,
                    "description",
                    consumer="default",
                    task_context=task,
                ),
                "status": status,
                "verification": criterion.verification,
                "predicate_id": (
                    criterion.predicate.predicate_id
                    if criterion.predicate is not None
                    else None
                ),
            }
            if status != "satisfied":
                # P2 milestone latch: plan-side display only. A criterion once
                # matched at a trusted target-app observation stays "已满足"
                # across transient current-observation staleness (keyboard
                # popup, partial overlays). Deterministic counter-evidence
                # (contradicted) unlocks. Acceptance never reads this field and
                # keeps strict current_observation freshness semantics.
                latch = goal_evidence.ever_matched(
                    ledger,
                    criterion_id=criterion.name,
                    contract_id=runtime_contract_id,
                )
                if latch.latched:
                    item["status"] = "satisfied"
                    item["latched"] = True
                    item["latched_epoch"] = latch.matched_epoch
            goal_agenda.append(item)
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

    preview_memory = update_gui_memory(
        state,
        current_app=current_app,
        screen_id=after_observation.snapshot.screen_id,
        reached_surface=after_observation.snapshot.foreground_activity,
        semantic_screen_id=after_observation.snapshot.semantic_screen_id,
    )
    # Liveness novelty reads the raw transition stream: visited_screens is
    # deduped for display, which would compress oscillation and hide "stuck".
    transition_stream = [
        {**item, "_transition_stream": True}
        for item in (preview_memory.get("screen_transition_stream") or [])
    ]
    current_liveness = trajectory_liveness(
        tried_actions=list(preview_memory.get("tried_actions") or []),
        visited_states=transition_stream or list(preview_memory.get("visited_screens") or []),
        criterion_history=goal_evidence.criterion_history_from_ledger(
            ledger, contract_id=runtime_contract_id
        ),
        budget={
            "novelty_exhaustion_steps": int(
                DEFAULT_VERIFICATION_POLICY.value("novelty_exhaustion_steps")
            )
        },
    )
    deterministic_reflection = _reflection_from_verifier(
        verifier_result,
        action=action_parsed,
        liveness=current_liveness,
        goal_agenda=goal_agenda,
        allow_skip=bool(
            configurable.get("skip_reflect_on_high_confidence", True)
        ),
    )
    model_skipped = deterministic_reflection is not None
    model_skip_reason = (
        deterministic_reflection.model_skip_reason
        if deterministic_reflection is not None
        else None
    )

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
            _sanitize_verifier_result_dict(
                verifier_result, task_context=task, consumer="reflect_prompt"
            )
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
        observation_before=before_verifier_observation,
        observation_after=after_verifier_observation,
    )
    action_succeeded = bool(reflection_fields["action_succeeded"])
    final_verdict = reflection_fields["reflection_verdict"]
    final_failure_cause = reflection_fields.get("failure_cause")
    # S4: a locate_* mark that was tapped and clearly did not take effect is
    # invalidated (state + trace) so the same wrong box cannot be re-rendered
    # into marks_block or re-tapped. The marks stay in the registry (D2
    # inheritance/versioning untouched); render and grounding filter them.
    newly_invalidated = _newly_invalidated_locate_marks(
        state,
        verdict=str(final_verdict or ""),
        failure_cause=final_failure_cause,
    )
    invalidated_mark_ids = sorted(
        {str(mark_id) for mark_id in (state.get("invalidated_mark_ids") or [])}
        | set(newly_invalidated)
    )
    if newly_invalidated:
        emit_trace(
            config,
            state,
            "reflect",
            "mark_invalidated",
            {
                "mark_ids": newly_invalidated,
                "verdict": final_verdict,
                "failure_cause": final_failure_cause,
            },
        )
    # Reflect judges one action and can never finish the task: only a finish
    # claim routed to the acceptance node can do that. A model that suggests
    # "finish" here is asking to emit a finish action next, not declaring
    # completion, so the suggestion is downgraded to keep planning.
    if parsed_reflection.suggested_strategy == "finish" and (
        verifier_result.hard_failure or final_verdict != "succeeded"
    ):
        parsed_reflection.suggested_strategy = "continue"

    context_updates = {"context_mode": context_mode}
    repeat_count = 0
    memory_mode: str | None = None
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
        outcome["disputed"] = bool(reflection_fields.get("disputed"))
        advisory = reflection_fields.get("verifier_advisory")
        if isinstance(advisory, dict) and advisory:
            # Verifier signals are advisory evidence for the next plan prompt:
            # the model keeps the verdict, but it must see what the verifier
            # observed so its judgement is grounded (model-delegation refactor 1.1).
            # Regex-redacted at write time: matched/missing postconditions can
            # echo raw screen text (e.g. a phone number) into the state copy.
            outcome["verifier_advisory"] = sanitize_context_payload(
                advisory,
                "verifier_advisory",
                consumer="inject",
                task_context=task if isinstance(task, str) else None,
            )
        existing_failure_memory = list(state.get("failure_memory") or [])
        # P3 #2: failure-memory writes are isolated by the arbitration result.
        # Disputed steps never write memory and never count as repeated
        # failures; consensus failures (verifier failure + model failure, or
        # hard failure) are verified; a model-alone failure with an unknown
        # verifier is written but flagged ``unverified``.
        memory_mode = failure_memory_write_mode(
            verifier_status=verifier_result.status,
            verdict=final_verdict,
            hard_failure=verifier_result.hard_failure,
            disputed=bool(reflection_fields.get("disputed")),
        )
        repeated = (
            detect_repeated_failure(existing_failure_memory, outcome)
            if memory_mode != "skip"
            else False
        )
        failure_memory = (
            update_failure_memory(
                existing_failure_memory,
                outcome,
                state.get("context_budget"),
                unverified=(memory_mode == "unverified"),
            )
            if memory_mode != "skip"
            else list(existing_failure_memory)
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
            screen_id=after_observation.snapshot.screen_id,
            reached_surface=after_observation.snapshot.foreground_activity,
            semantic_screen_id=after_observation.snapshot.semantic_screen_id,
        )
        previous_progress = (state.get("gui_memory") or {}).get("task_progress") or {}
        stuck_rounds = (
            int(previous_progress.get("stuck_rounds") or 0) + 1
            if current_liveness["state"] == "stuck"
            else 0
        )
        context_updates["gui_memory"]["task_progress"] = {
            **dict(
                context_updates["gui_memory"].get("task_progress") or {}
            ),
            "trajectory_liveness": current_liveness["state"],
            "liveness_reasons": current_liveness["reasons"],
            "novelty_streak": current_liveness["novelty_streak"],
            "stuck_rounds": stuck_rounds,
        }
        # Trajectory-level check, deliberately separate from `detect_repeated_failure`:
        # a loop where every step verifies as successful is invisible to failure memory,
        # so re-using one target on one surface is judged on its own terms here.
        tried_actions = list(context_updates["gui_memory"].get("tried_actions") or [])
        context_updates["repeated_action_detected"] = bool(
            tried_actions
            and detect_repeated_action(tried_actions[:-1], tried_actions[-1])
        )
        if tried_actions:
            repeat_key = repeated_action_key(tried_actions[-1])
            if repeat_key is not None:
                repeat_count = sum(
                    1
                    for item in tried_actions
                    if repeated_action_key(item) == repeat_key
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
            "reflection_directive_filtered": parsed_reflection.directive_filtered,
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
            "repeated_action_detected": context_updates.get(
                "repeated_action_detected", False
            ),
            "disputed": bool(reflection_fields.get("disputed")),
            "failure_memory_write_mode": memory_mode,
            "verifier_advisory": (
                sanitize_context_payload(
                    reflection_fields.get("verifier_advisory"),
                    "verifier_advisory",
                    consumer="trace_payload",
                    task_context=task if isinstance(task, str) else None,
                )
                if isinstance(reflection_fields.get("verifier_advisory"), dict)
                else None
            ),
            "repeat_count": repeat_count,
            "trajectory_liveness": current_liveness["state"],
            "stuck_rounds": (
                (context_updates.get("gui_memory") or {})
                .get("task_progress", {})
                .get("stuck_rounds", 0)
            ),
            "model_skipped": model_skipped,
            "model_skip_reason": model_skip_reason,
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
        "reflection_directive_filtered": parsed_reflection.directive_filtered,
        "disputed": bool(reflection_fields.get("disputed")),
        "model_skipped": model_skipped,
        "model_skip_reason": model_skip_reason,
        "verifier_result": verifier_result_dict,
        "verifier_status": verifier_result.status,
        "verifier_failure_cause": verifier_result.failure_cause,
        "verifier_evidence": verifier_evidence,
        "goal_evidence_ledger": ledger,
        "goal_agenda": goal_agenda,
        "observation_retry_count": 0,
        "invalidated_mark_ids": invalidated_mark_ids,
        # Reflect judges a single action and never completes the task; only the
        # acceptance node can set this True, after the goal gate passes.
        "finished": False,
        **context_updates,
    }
