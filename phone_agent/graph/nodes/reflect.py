"""Reflect node: screenshot → structured action outcome reflection."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from phone_agent.device_factory import ObservationCaptureError
from phone_agent.config.policy import (
    DEFAULT_VERIFICATION_POLICY,
    STAGE_STALL_RECOMPILE_WINDOWS,
)
from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    action_had_effect,
    build_action_outcome_summary,
    build_screen_belief,
    consecutive_no_effect_count,
    context_enabled,
    detect_repeated_action,
    detect_repeated_failure,
    failure_memory_write_mode,
    get_context_mode,
    normalize_failure_cause,
    sanitize_context_payload,
    repeated_action_key,
    select_reflect_context,
    stage_stall_recompile,
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
from phone_agent.graph.goal_evaluator import _normalize_criterion_name
from phone_agent.graph import goal_evidence
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
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id 或屏幕上的具体元素","observed_value":"你在该处实际看到的文字"}],"criteria_observations":[{"criterion":"criterion_name","status":"observed|not_visible|contradicted","observed_value":"读到的值（可省略）"}]}

判断标准：
1. 动作生效：页面满足预期后置条件（如输入框聚焦、目标文本出现、目标页面打开、目标应用打开）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"任务已完成","named_evidence":[{"criterion":"成功标准名","screen_reference":"屏幕证据引用"}]}

重要约束：
- message 只描述当前屏幕观察到的客观状态；禁止行动指令、禁止目标名/输入内容建议。
- named_evidence 仅在 suggested_strategy="finish" 时需要输出，且只列出契约中标记为 [judge] 的成功标准。标记为 [auto] 的标准由系统读取设备状态自行核验，你不需要点名或回报。
- 每条证据给出：criterion（标准名）、screen_reference（mark_id 或屏幕上的具体元素，不要写"区域1"/"屏幕"这类占位）、observed_value（你在该处实际看到的原文）。照实回报你看到的文字即可，不要猜测系统内部使用的取值。observed_value 仅用于当前 node 匹配，不写入 state/trace。
- criteria_observations 是**读屏报告**，与 verdict（本动作是否生效）相互独立：verdict 只回答动作是否生效，criteria_observations 只报告判据内容在当前屏幕是否可读。只对用户消息"判据观察清单"中列出的判据输出，每条判据最多一条；若清单为空或未提供，不要输出该字段。
- criteria_observations 状态语义：observed=当前屏幕直接读到该判据的内容，observed_value 填读到的值；not_visible=当前屏幕读不到该判据内容（不要推断、不要猜测）；contradicted=屏幕上读到与该判据矛盾的值（必须给 observed_value）。照实回报，不要为了让任务看起来完成而虚报 observed。
- 只有在截图明确显示加载中、空白页、网络错误、进度条/转圈、或执行结果表示应用无响应时，才使用 failure_cause="network_or_loading" 和 suggested_strategy="wait"。
- 如果刚执行的是 Launch/启动应用，且当前屏幕信息或截图已显示目标应用/设置页/目标页面已打开，即使任务还没完成，也应判定为 succeeded + continue，而不是 partial + wait。
- 不要因为页面内容很多、设置项列表尚需下一步操作，就误判为加载中；可继续操作的稳定页面应输出 continue。
- 广告、banner、推荐流、热词、计数器或首页动态内容变化只能作为噪声，不能单独证明 Tap/Type/搜索/打开视频成功；必须引用后置条件证据。
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded.

You MUST output exactly one JSON object. Do not output Markdown, XML, function calls, or extra text:
{"verdict":"succeeded|failed|partial","failure_cause":"none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown","suggested_strategy":"continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish","message":"xxx","named_evidence":[{"criterion":"criterion_name","screen_reference":"mark_id or a concrete on-screen element","observed_value":"the text you actually see there"}],"criteria_observations":[{"criterion":"criterion_name","status":"observed|not_visible|contradicted","observed_value":"the value you read (optional)"}]}

Judgment criteria:
1. Action succeeded: expected postconditions are satisfied (for example focused input, expected text, target page, or target app)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output {"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed","named_evidence":[{"criterion":"criterion_name","screen_reference":"screen evidence reference"}]}

Important constraints:
- message describes only the objective state observed on the current screen; no action instructions, no target names, and no input-content suggestions.
- named_evidence is only required when suggested_strategy="finish", and only for criteria marked [judge] in the contract. Criteria marked [auto] are verified by the system from device state — do not cite or report them.
- For each evidence item give: criterion (its name), screen_reference (a mark_id or concrete on-screen element — never a placeholder like "region-1"/"screen"), and observed_value (the text you actually see there). Report what you see verbatim; do not guess values the system uses internally. observed_value is node-local and must not enter state or trace.
- criteria_observations is a SCREEN-READING report, independent of the verdict (whether this action took effect): verdict only answers the action question; criteria_observations only reports whether each criterion's content is readable on the current screen. Report only the criteria listed in the user message's "criteria observation list", at most once per criterion; omit the field entirely when the list is absent or empty.
- criteria_observations status semantics: observed = you directly read that criterion's content on the current screen (fill observed_value with what you read); not_visible = the current screen does not show it (do not infer or guess); contradicted = the screen shows a value contradicting the criterion (observed_value required). Report honestly — never claim observed just to make the task look complete.
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


# P4: reflection messages that explicitly state the tap target's state did
# not change (未选中/未生效) — the trigger for invalidating a wrong locate_*
# box when the reflection is partial but the failure_cause is NOT
# coordinate_or_tap_offset (the empirical S4 gap: the model correctly reads
# "not selected" without naming a coordinate offset). Conservative explicit
# markers only; generic phrases like "no change"/"没有变化" are not enough.
_TARGET_STATE_UNCHANGED_CN_MARKERS = (
    "未选中",
    "没选中",
    "没有被选中",
    "未生效",
    "没有生效",
    "未起作用",
    "没有选中目标",
)
_TARGET_STATE_UNCHANGED_EN_MARKERS = (
    "not selected",
    "was not selected",
    "not activated",
    "not applied",
    "did not take effect",
    "didn't take effect",
    "not effective",
    "not toggled",
)


def _states_target_unchanged(message: str | None) -> bool:
    text = str(message or "").casefold()
    if not text:
        return False
    return any(marker in text for marker in _TARGET_STATE_UNCHANGED_CN_MARKERS) or any(
        marker in text for marker in _TARGET_STATE_UNCHANGED_EN_MARKERS
    )


def _newly_invalidated_locate_marks(
    state: dict[str, Any],
    *,
    verdict: str,
    failure_cause: str | None,
    reflection_message: str | None = None,
) -> list[str]:
    """S4/P4: invalidate a tapped ``locate_*`` mark whose tap clearly did not land.

    The empirical failure mode is one wrong LA box tapped repeatedly (burning
    the locate budget). Only ``locate_*`` marks are invalidated — LA boxes may
    be wrong; accessibility-origin marks are structural and never invalidated.

    Rules (deliberately narrow; ``partial`` alone is not enough):
    - verdict ``failed`` → the action did not take effect; the box is suspect.
    - verdict ``partial`` with ``coordinate_or_tap_offset`` → the page changed
      but the tap clearly landed at the wrong place; the box is suspect.
    - verdict ``partial`` whose reflection message explicitly states the
      target state did not change (未选中/未生效 / not selected / not applied)
      → the tap did not select/activate the target; the box is suspect.
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
    if verdict == "partial":
        if failure_cause == "coordinate_or_tap_offset":
            return [mark_id]
        if _states_target_unchanged(reflection_message):
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
    # S1: model screen-readings of the unsatisfied-criteria list. Pure form
    # validation here (criterion name + status); the observed_value is
    # redacted before it enters the ledger.
    criteria_observations: list[dict[str, object]] | None = None
    # P5 #1: set when the model call was skipped (deterministic path). The
    # reason distinguishes hard_failure from verifier high-confidence success
    # so the reflect_result trace can explain why no model call happened.
    model_skipped: bool = False
    model_skip_reason: str | None = None


def _judge_evidence_pending(
    goal_agenda: list[dict] | None, ledger: list[dict] | None, contract_id: str
) -> bool:
    """Whether any goal-contract vlm_judge criterion still awaits evidence.

    vlm_judge criteria are settled by model screen-readings
    (``criteria_observations`` → ``model_observation`` ledger entries), so the
    deterministic reflect skip must not fire while such a criterion has no
    ``observed`` reading — the model still needs to observe the screen to
    collect that evidence.
    """

    for item in goal_agenda or []:
        if str(item.get("verification") or "") != "vlm_judge":
            continue
        if str(item.get("status") or "") == "satisfied":
            continue
        name = str(item.get("name") or "")
        if name and goal_evidence.criterion_observed_in_ledger(
            ledger or [], contract_id=contract_id, criterion=name
        ):
            continue
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
    goal_ledger: list[dict] | None = None,
    goal_contract_id: str = "",
    allow_skip: bool = True,
) -> ReflectionResult | None:
    """Skip model reflection for self-evident action questions (P5 #1).

    The verifier's deterministic success signals answer "did this action take
    effect?" without a vision-language call. The skip is gated by:
    1. ``hard_failure`` never skips — it produces the deterministic failure.
    2. A pending vlm_judge criterion (``_judge_evidence_pending``, which now
       reads the model screen-readings ledger) forces the model call so the
       screen-reading evidence can still be collected.
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
    if _judge_evidence_pending(
        goal_agenda, goal_ledger, str(goal_contract_id or "")
    ):
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
    criteria_observations = _parse_criteria_observations(data)
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
        criteria_observations=criteria_observations,
    )


# S1: model screen-readings. Form validation only — the model owns the
# content (status semantics are documented in the reflect system prompt).
VALID_CRITERIA_OBSERVATION_STATUSES = frozenset(
    {"observed", "not_visible", "contradicted"}
)


def _parse_criteria_observations(data: dict) -> list[dict[str, object]] | None:
    """Extract and form-validate the model's screen readings."""

    raw = data.get("criteria_observations") if isinstance(data, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    observations: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion") or "").strip()[:128]
        status = str(item.get("status") or "")
        if not criterion or status not in VALID_CRITERIA_OBSERVATION_STATUSES:
            continue
        entry: dict[str, object] = {"criterion": criterion, "status": status}
        if item.get("observed_value") is not None:
            entry["observed_value"] = str(item.get("observed_value"))[:200]
        observations.append(entry)
    return observations
















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


# S1: the model is the only screen-content reader. Code hands it the list of
# criteria still awaiting a screen reading (form only: name + description +
# provenance annotation + control hint); the model reports observed /
# not_visible / contradicted per criterion. App-foreground criteria are
# [auto] — settled by the exact-and-free code sensor, never asked of the model.
_MAX_CRITERIA_OBSERVATION_ROWS = 6


def _criteria_observation_prompt_block(
    *,
    contract,
    ledger: list[dict],
    contract_id: str,
    satisfied_by_code: set[str],
    lang: str,
) -> str:
    """Render the unsatisfied-criteria list for the reflect model to screen-read."""

    if contract is None or not contract.task_plan:
        return ""
    rows: list[str] = []
    for criterion in contract.success_criteria:
        name = criterion.name
        if name in satisfied_by_code:
            continue
        if goal_evidence._criterion_app_foreground(criterion):
            continue
        entry = goal_evidence.latest_model_observation(
            ledger, contract_id=contract_id, criterion=name
        )
        if entry is not None and entry.get("status") == "observed":
            continue
        provenance_tag = {
            "state": "",
            "confirmed": "[需确认]" if lang == "cn" else "[confirm]",
            "caused": "[造成]" if lang == "cn" else "[caused]",
        }.get(str(getattr(criterion, "provenance", "state") or "state"), "")
        line = f"  - {name} {provenance_tag}: {criterion.description}".strip()
        control_hint = str(getattr(criterion, "control_hint", None) or "")
        if control_hint:
            line = f"{line} (control: {control_hint})" if lang == "en" else f"{line}（控件：{control_hint}）"
        rows.append(line)
        if len(rows) >= _MAX_CRITERIA_OBSERVATION_ROWS:
            break
    if not rows:
        return ""
    rows_text = "\n".join(rows)
    if lang == "en":
        return (
            "Criteria observation list (screen-read each criterion on the "
            "current screenshot; at most one criteria_observations entry per "
            "criterion; observed=read the content and give the value, "
            "not_visible=not on screen (never infer), contradicted=the screen "
            "shows a contradicting value and you must give it):\n"
            f"{rows_text}"
        )
    return (
        "需要读屏观察的判据（在截图上逐条读屏；每条判据最多输出一条 "
        "criteria_observations；observed=读到内容并给出值，not_visible=屏幕读不到"
        "（不推断），contradicted=读到矛盾值并必须给出值）：\n"
        f"{rows_text}"
    )


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
        learning=configurable.get("app_learning_context"),
    )
    # F7: learn (user term -> resolved package) only after the verifier
    # confirms the foreground matches this step's launch — am-start success
    # alone never records (the old launch-tool record could learn a wrong
    # mapping and self-certify the launch in this same run). The resolved
    # mapping is carried in action_result metadata by the Launch tool.
    if (
        isinstance(action_parsed, dict)
        and str(action_parsed.get("action") or "") == "Launch"
        and isinstance(getattr(verifier_result, "signals", None), dict)
        and verifier_result.signals.get("launch_matched") is True
        and isinstance(action_result, dict)
    ):
        launch_metadata = action_result.get("metadata")
        term = (
            launch_metadata.get("launch_app_term")
            if isinstance(launch_metadata, dict)
            else None
        )
        package = (
            launch_metadata.get("launch_resolved_package")
            if isinstance(launch_metadata, dict)
            else None
        )
        app_learning = configurable.get("app_learning_context")
        if term and package and app_learning is not None:
            app_learning.record(str(term), str(package))
    runtime_contract_id = goal_runtime_reference(state)
    ledger = list(state.get("goal_evidence_ledger") or [])
    # F6: snapshot the ledger before this step's model screen-reads are
    # appended, so the per-step "fresh observation" signal compares each read
    # against the criterion's PREVIOUS record (status change), not against
    # this step's own appended entries.
    ledger_before_step = list(ledger)
    goal_contract = ensure_goal_contract(state, config)
    # S6: the fact-provider evidence path is retired — the model screen-read
    # (criteria_observations) is the only content sensor. The app-foreground
    # code check survives (exact and free): it resolves the target app against
    # the device registry (P0 #8) and feeds the code-settled criteria.
    in_target_app = False
    if goal_contract is not None:
        in_target_app = goal_evidence.target_app_entered(
            goal_contract,
            None,
            current_app=current_app,
            foreground_activity=after_observation.snapshot.foreground_activity,
        )
        # L1: mechanically extract this screen's accessibility text digest
        # (judge prompt context only — never an evidence gate).
        ledger = goal_evidence.append_screen_text_digest(
            ledger,
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            marks=after_observation.mark_registry.marks.values(),
            target_app_entered=in_target_app,
        )
        # Positive model counter-observation revokes the seal (P0 #13a:
        # revocation only on contradiction, never on absence).
        contradicted = set()
        for entry in ledger:
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") != "model_observation":
                continue
            if entry.get("contract_id") != runtime_contract_id:
                continue
            if entry.get("status") == "contradicted":
                contradicted.add(str(entry.get("criterion") or ""))
        if contradicted:
            ledger = goal_evidence.revoke_seals_on_contradiction(
                ledger,
                contract=goal_contract,
                contract_id=runtime_contract_id,
                contradicted_criteria=contradicted,
                screen_id=after_observation.snapshot.screen_id,
                step=state.get("step_count"),
            )
    goal_agenda: list[dict] = []
    # S1: app-foreground criteria are settled by the exact-and-free code
    # sensor (target-app foreground check) — they never need a model read.
    satisfied_by_code: set[str] = set()
    if goal_contract is not None and in_target_app:
        satisfied_by_code = {
            criterion.name
            for criterion in goal_contract.success_criteria
            if goal_evidence._criterion_app_foreground(criterion)
        }
    if goal_contract is not None:
        for criterion in goal_contract.success_criteria:
            item: dict = {
                "name": criterion.name,
                "description": sanitize_context_payload(
                    criterion.description,
                    "description",
                    consumer="default",
                    task_context=task,
                ),
                "status": "pending",
                "verification": criterion.verification,
                "predicate_id": (
                    criterion.predicate.predicate_id
                    if criterion.predicate is not None
                    else None
                ),
            }
            if goal_evidence._criterion_app_foreground(criterion) and in_target_app:
                item["status"] = "satisfied"
            else:
                observation = goal_evidence.latest_model_observation(
                    ledger,
                    contract_id=runtime_contract_id,
                    criterion=criterion.name,
                )
                if observation is not None and observation.get("status") == "observed":
                    item["status"] = "satisfied"
                    item["latched"] = True
                    if isinstance(observation.get("observation_epoch"), int):
                        item["latched_epoch"] = observation["observation_epoch"]
            goal_agenda.append(item)
    # W2 T3: fold the same ledger into per-stage task-plan status. Pure ledger
    # fold — zero additional model calls; reflect's verdict semantics are
    # untouched (stages are belief/telemetry, never a gate).
    task_plan_status = None
    if goal_contract is not None and goal_contract.task_plan:
        task_plan_status = goal_evidence.stage_status_from_ledger(
            ledger,
            goal_contract.task_plan,
            contract_id=runtime_contract_id,
            criteria={
                criterion.name: criterion
                for criterion in goal_contract.success_criteria
            },
            satisfied_by_code=satisfied_by_code,
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
        goal_ledger=ledger,
        goal_contract_id=runtime_contract_id,
        allow_skip=bool(
            configurable.get("skip_reflect_on_high_confidence", True)
        ),
    )
    # W2 T6 + P3: single needs_recompile write point — stage stall. When the
    # current stage has not advanced for K consecutive reflect windows AND the
    # trajectory is stuck, the plan is a bad belief; flag recompilation so the
    # existing replan→goal route rebuilds it. P3: the first K windows right
    # after a recompile are immune (grace_windows); goal_node re-arms the grace
    # counter and resets the stall counter when the recompile completes.
    stage_stall_windows, stage_recompile, stage_stall_grace = stage_stall_recompile(
        previous_status=state.get("task_plan_status"),
        current_status=task_plan_status,
        liveness_state=current_liveness["state"],
        stall_windows=int(state.get("stage_stall_windows") or 0),
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=int(state.get("stage_stall_grace_windows") or 0),
    )
    if stage_recompile:
        emit_trace(
            config,
            state,
            "reflect",
            "stage_stall_recompile",
            {
                "stage_stall_windows": stage_stall_windows,
                "threshold": STAGE_STALL_RECOMPILE_WINDOWS,
                "trajectory_liveness": current_liveness["state"],
                "current_stage_index": task_plan_status.get("current_stage_index")
                if task_plan_status
                else None,
            },
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
    criteria_observation_block = ""
    if deterministic_reflection is None:
        criteria_observation_block = _criteria_observation_prompt_block(
            contract=goal_contract,
            ledger=ledger,
            contract_id=runtime_contract_id,
            satisfied_by_code=satisfied_by_code,
            lang=lang,
        )
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
            if criteria_observation_block:
                reflect_text = f"{reflect_text}\n{criteria_observation_block}\n\n"
            reflect_text = (
                f"{reflect_text}"
                f"Output JSON with action_effect, task_progress, matched_postconditions, "
                f"missing_postconditions, dynamic_change_only, evidence, next_strategy, named_evidence"
                f", criteria_observations."
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
            if criteria_observation_block:
                reflect_text = f"{reflect_text}\n{criteria_observation_block}\n\n"
            reflect_text = (
                f"{reflect_text}"
                f"请输出 JSON，字段为 action_effect、task_progress、matched_postconditions、"
                f"missing_postconditions、dynamic_change_only、evidence、next_strategy、named_evidence"
                f"、criteria_observations。"
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
        reflection_message=str(reflection_state_value or ""),
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

    # --- Stage-Sealing tail (side effects only; P0 #13 single-step verdict
    # semantics are untouched above) ---
    # S1: the model's screen-readings join the ledger. Form-only storage;
    # observed_value is redacted on write (P0 #10) and the model owns the
    # content interpretation. Empty when the deterministic path skipped the
    # model call — those criteria stay pending (fail-closed).
    if parsed_reflection.criteria_observations:
        ledger = goal_evidence.append_model_observations(
            ledger,
            contract_id=runtime_contract_id,
            observations=parsed_reflection.criteria_observations,
            step=step_count,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            semantic_keys={
                criterion.name: goal_evidence.criterion_semantic_key(
                    criterion.description
                )
                for criterion in goal_contract.success_criteria
            }
            if goal_contract is not None
            else None,
        )
        # Positive model counter-observation on a sealed criterion revokes the
        # seal (P0 #13a: revocation only on contradiction, never on absence).
        if goal_contract is not None:
            contradicted = {
                str(item.get("criterion") or "")
                for item in parsed_reflection.criteria_observations
                if str(item.get("status") or "") == "contradicted"
            }
            if contradicted:
                ledger = goal_evidence.revoke_seals_on_contradiction(
                    ledger,
                    contract=goal_contract,
                    contract_id=runtime_contract_id,
                    contradicted_criteria=contradicted,
                    screen_id=after_observation.snapshot.screen_id,
                    step=step_count,
                )
    # L2: promote a succeeded/partial action to an effect event. The event is
    # judge context (authority below L1), never a gate on its own.
    if goal_contract is not None and goal_evidence.should_record_effect_event(
        verdict=str(final_verdict or ""),
        hard_failure=bool(verifier_result.hard_failure),
    ):
        action_dict = action_parsed if isinstance(action_parsed, dict) else {}
        target_value = None
        grounding_target = (state.get("grounding_observation") or {}).get("target") or {}
        if isinstance(grounding_target, dict):
            target_value = grounding_target.get("mark_id") or grounding_target.get(
                "text"
            )
        matched_codes = (
            list(
                (verifier_result.evidence or {}).get("matched_postconditions") or []
            )
            or None
        )
        named_keys: dict[str, str] = {}
        for item in parsed_reflection.named_evidence or []:
            name = str(item.get("criterion") or "")
            if not name:
                continue
            for criterion in goal_contract.success_criteria:
                if _normalize_criterion_name(criterion.name) == _normalize_criterion_name(
                    name
                ):
                    named_keys.setdefault(
                        name,
                        goal_evidence.criterion_semantic_key(criterion.description),
                    )
                    break
        ledger = goal_evidence.append_effect_event(
            ledger,
            contract_id=runtime_contract_id,
            action=str(action_dict.get("action") or ""),
            target=str(target_value) if target_value else None,
            observed_after=(
                f"verdict={final_verdict} postconditions={','.join(matched_codes)}"
                if matched_codes
                else f"verdict={final_verdict}"
            ),
            screen_id=after_observation.snapshot.screen_id,
            step=step_count,
            named_evidence=parsed_reflection.named_evidence,
            semantic_keys=named_keys or None,
            target_app_entered=in_target_app or None,
        )
    # Eager sealing: a stage whose done criteria are all latched is sealed
    # once (idempotent by semantic key). Sealed criteria are authoritative in
    # later folds until a positive counter-observation revokes the seal.
    if goal_contract is not None and goal_contract.task_plan:
        ledger, new_seals = goal_evidence.seal_satisfied_stages(
            ledger,
            contract=goal_contract,
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            step=step_count,
            evidence_refs=[
                str(after_observation.snapshot.screen_id),
                f"step:{step_count}",
            ],
            satisfied_by_code=satisfied_by_code,
        )
        for seal in new_seals:
            emit_trace(
                config,
                state,
                "reflect",
                "stage_sealed",
                {
                    "stage_id": seal["stage_id"],
                    "criteria_sealed": seal["criteria_sealed"],
                    "semantic_key": seal["semantic_key"],
                    "step": seal["step"],
                },
            )
        task_plan_status = goal_evidence.stage_status_from_ledger(
            ledger,
            goal_contract.task_plan,
            contract_id=runtime_contract_id,
            criteria={
                criterion.name: criterion
                for criterion in goal_contract.success_criteria
            },
            satisfied_by_code=satisfied_by_code,
        )
    ledger = goal_evidence.bounded_evidence_ledger(ledger)
    # L4: plan-side per-criterion gap list (fold-accurate status + provenance
    # requirement). Pure ledger fold over the model screen-readings plus the
    # app-foreground code sensor; descriptions are inject-sanitized before
    # they reach state (the contract descriptions are already regex-redacted,
    # this is the defensive re-sanitization of the egress channel).
    criterion_gap_list = None
    if goal_contract is not None and goal_contract.task_plan:
        gap_raw = goal_evidence.criterion_gap_status(
            contract=goal_contract,
            ledger=ledger,
            contract_id=runtime_contract_id,
            screen_id=after_observation.snapshot.screen_id,
            observation_epoch=after_observation.snapshot.observation_epoch,
            satisfied_by_code=satisfied_by_code,
        )
        if gap_raw:
            criterion_gap_list = sanitize_context_payload(
                gap_raw,
                "criterion_gap_list",
                consumer="inject",
                task_context=task if isinstance(task, str) else None,
            )

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
        # Effect-guards: the authoritative per-step effect signal for the
        # tried_actions entry. Reflect judges from the signals it already owns:
        # before/after screen hash (previous observation vs this capture), the
        # number of FRESH model screen-reads appended to the ledger this step
        # (status changed vs the criterion's previous record — a raw count
        # would be nearly always > 0 with a goal contract, see F6), and the
        # verdict. A productive step resets the same-key
        # consecutive-no-effect streak; a dead loop keeps it rising.
        # The before-frame hash lives at the state top level: plan writes
        # state["screen_hash"] as the pre-action frame every step, while
        # Observation.to_dict() has no top-level screen_hash (it nests under
        # ["snapshot"]["screen_hash"]) — reading the observation dict here
        # would always see None in real flights.
        before_screen_hash = state.get("screen_hash")
        fresh_observation_count = goal_evidence.fresh_observation_count(
            parsed_reflection.criteria_observations,
            ledger_before_step,
            contract_id=runtime_contract_id,
        )
        had_effect = action_had_effect(
            before_screen_hash=before_screen_hash,
            after_screen_hash=after_observation.snapshot.screen_hash,
            new_observation_count=fresh_observation_count,
            verdict=final_verdict,
        )
        context_updates["gui_memory"] = update_gui_memory(
            {**state, **context_updates, "action_result": action_result},
            current_app=current_app,
            screen_id=after_observation.snapshot.screen_id,
            reached_surface=after_observation.snapshot.foreground_activity,
            semantic_screen_id=after_observation.snapshot.semantic_screen_id,
            had_effect=had_effect,
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
                repeat_count = consecutive_no_effect_count(tried_actions, repeat_key)

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
            "criteria_observations": [
                {
                    "criterion": str(item.get("criterion") or ""),
                    "status": str(item.get("status") or ""),
                }
                for item in (parsed_reflection.criteria_observations or [])
            ],
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
        "task_plan_status": task_plan_status,
        "criterion_gap_list": criterion_gap_list,
        "stage_stall_windows": stage_stall_windows,
        "stage_stall_grace_windows": stage_stall_grace,
        **({"needs_recompile": True} if stage_recompile else {}),
        "observation_retry_count": 0,
        "invalidated_mark_ids": invalidated_mark_ids,
        # Reflect judges a single action and never completes the task; only the
        # acceptance node can set this True, after the goal gate passes.
        "finished": False,
        **context_updates,
    }
