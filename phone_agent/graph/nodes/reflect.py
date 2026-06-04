"""Reflect node: screenshot → structured action outcome reflection."""

import ast
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
    update_gui_memory,
    update_failure_memory,
    update_summarized_history,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.graph.verifier import merge_verifier_with_reflection, verify_action_outcome
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效，并给出下一步建议。

你必须严格按照要求输出以下格式：
<think>{think}</think>
<answer>{action}</answer>

其中：
- {think} 是你的推理过程。
- {action} 必须优先使用：reflection(verdict="succeeded|failed|partial", failure_cause="none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown", suggested_strategy="continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish", message="xxx")
- 兼容旧格式：continue(message="xxx") 表示动作生效；retry(message="xxx") 表示动作未生效

判断标准：
1. 动作生效：页面如预期发生了变化（如点击后跳转、输入后文本出现、滑动后内容变化）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 reflection(verdict="succeeded", failure_cause="none", suggested_strategy="finish", message="任务已完成")

重要约束：
- 只有在截图明确显示加载中、空白页、网络错误、进度条/转圈、或执行结果表示应用无响应时，才使用 failure_cause="network_or_loading" 和 suggested_strategy="wait"。
- 如果刚执行的是 Launch/启动应用，且当前屏幕信息或截图已显示目标应用/设置页/目标页面已打开，即使任务还没完成，也应判定为 succeeded + continue，而不是 partial + wait。
- 不要因为页面内容很多、设置项列表尚需下一步操作，就误判为加载中；可继续操作的稳定页面应输出 continue。
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded, then give next-step advice.

You MUST strictly output in the following format:
<think>{think}</think>
<answer>{action}</answer>

Where:
- {think} is your reasoning process.
- {action} should use: reflection(verdict="succeeded|failed|partial", failure_cause="none|element_not_found|wrong_page|app_not_responding|network_or_loading|permission_or_login_or_captcha|unsafe_or_sensitive|coordinate_or_tap_offset|context_lost|repeated_action|model_parse_failed|unknown", suggested_strategy="continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish", message="xxx")
- Legacy compatible formats are accepted: continue(message="xxx") or retry(message="xxx")

Judgment criteria:
1. Action succeeded: the page changed as expected (e.g., navigated after tap, text appeared after input, content changed after swipe)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output reflection(verdict="succeeded", failure_cause="none", suggested_strategy="finish", message="Task completed")

Important constraints:
- Use failure_cause="network_or_loading" and suggested_strategy="wait" only when the screenshot clearly shows loading, a blank page, a network error, a spinner/progress indicator, or the execution result indicates the app is not responding.
- If the action just executed is Launch and the current screen info or screenshot already shows the target app/settings/target page is open, judge it as succeeded + continue even if the overall task still needs more steps; do not return partial + wait.
- Do not treat a stable page with many settings/list items as loading. If the page is actionable, return continue.
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


@dataclass
class ReflectionResult:
    verdict: str
    failure_cause: str | None
    suggested_strategy: str | None
    message: str


def _literal_kwargs(raw_action: str) -> tuple[str, dict[str, object]]:
    expression = ast.parse(raw_action, mode="eval").body
    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
        raise ValueError("Reflection action must be a function call")
    return expression.func.id, {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in expression.keywords
        if keyword.arg is not None
    }


def parse_reflection_action(raw_action: str) -> ReflectionResult:
    """Parse structured reflection output with safe Python literal parsing."""
    raw_action = raw_action.strip()
    if raw_action.startswith("reflection("):
        try:
            name, kwargs = _literal_kwargs(raw_action)
        except (SyntaxError, ValueError):
            return ReflectionResult("failed", "unknown", "retry", raw_action)
        if name != "reflection":
            raise ValueError("Unknown reflection function")
        verdict = str(kwargs.get("verdict", "failed"))
        failure_cause = str(kwargs.get("failure_cause", "unknown"))
        suggested_strategy = str(kwargs.get("suggested_strategy", "retry"))
        message = str(kwargs.get("message", raw_action))
        if verdict not in VALID_VERDICTS:
            verdict = "failed"
        failure_cause = normalize_failure_cause(failure_cause)
        if suggested_strategy not in VALID_STRATEGIES:
            suggested_strategy = "retry"
        if verdict == "succeeded" and failure_cause == "none":
            parsed_cause = None
        else:
            parsed_cause = failure_cause
        return ReflectionResult(verdict, parsed_cause, suggested_strategy, message)

    if raw_action.startswith("continue"):
        return ReflectionResult("succeeded", None, "continue", raw_action)
    if raw_action.startswith("retry"):
        return ReflectionResult("failed", "unknown", "retry", raw_action)
    return ReflectionResult("failed", "unknown", "retry", raw_action)


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
    step_count = state["step_count"]
    max_steps = state["max_steps"]
    context_mode = get_context_mode(state, config)

    # 1. Capture screen again
    screenshot = device_factory.get_screenshot(device_id)
    current_app = device_factory.get_current_app(device_id)
    verifier_result = verify_action_outcome(
        before_state=state,
        after_screenshot=screenshot,
        after_app=current_app,
        action_result=action_result,
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
            "verifier_result": verifier_result.to_dict(),
        },
    )

    # 2. Build reflection prompt with language selection
    if lang == "en":
        system_prompt = REFLECT_SYSTEM_PROMPT_EN
    else:
        system_prompt = REFLECT_SYSTEM_PROMPT_CN

    action_str = str(sanitize_context_payload(action_parsed)) if action_parsed else "None"
    result_str = str(sanitize_context_payload(action_result)) if action_result else "None"

    screen_info = MessageBuilder.build_screen_info(current_app)
    if lang == "en":
        reflect_text = (
            f"Original task: {task}\n"
            f"Current step: {step_count} / {max_steps}\n"
            f"Action just executed: {action_str}\n"
            f"Execution result: {result_str}\n"
            f"Current screen info: {screen_info}\n\n"
            f"Please observe the current screenshot, judge if the action succeeded, and give next-step advice."
        )
    else:
        reflect_text = (
            f"原始任务：{task}\n"
            f"当前步数：{step_count} / {max_steps}\n"
            f"刚执行的动作：{action_str}\n"
            f"执行结果：{result_str}\n"
            f"当前屏幕信息：{screen_info}\n\n"
            f"请观察当前截图，判断动作是否生效，并给出下一步建议。"
        )

    reflect_messages = [
        MessageBuilder.create_system_message(system_prompt),
        MessageBuilder.create_user_message(
            text=reflect_text,
            image_base64=screenshot.base64_data,
            image_mime_type=getattr(screenshot, "mime_type", "image/png"),
        ),
    ]

    # 3. Model inference for reflection
    try:
        try:
            response = model_client.request(
                reflect_messages, output_mode="text_dsl", validate_action=False
            )
        except TypeError as type_error:
            if "output_mode" not in str(type_error) and "validate_action" not in str(type_error):
                raise
            response = model_client.request(reflect_messages)
    except Exception as e:
        error_message = f"Reflection failed: {type(e).__name__}"
        if verbose:
            print(error_message)
        if verifier_result.hard_failure:
            fallback_succeeded = False
            fallback_verdict = "failed"
            fallback_failure_cause = verifier_result.failure_cause or "unknown"
        elif verifier_result.status == "success" and verifier_result.confidence >= 0.9:
            fallback_succeeded = True
            fallback_verdict = "succeeded"
            fallback_failure_cause = None
        else:
            fallback_succeeded = False
            fallback_verdict = "failed"
            fallback_failure_cause = "model_reflection_failed"
        emit_trace(config, state, "reflect", "reflect_error", {"message": error_message})
        return {
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "verifier_result": verifier_result.to_dict(),
            "verifier_status": verifier_result.status,
            "verifier_failure_cause": verifier_result.failure_cause,
            "reflection": error_message,
            "action_succeeded": fallback_succeeded,
            "reflection_verdict": fallback_verdict,
            "failure_cause": fallback_failure_cause,
            "suggested_strategy": "continue",
            "retry_count": int(state.get("retry_count") or 0) + (0 if fallback_succeeded else 1),
            "finished": False,
            "context_mode": context_mode,
        }

    # 4. Parse reflection
    raw_action = response.action.strip()
    parsed_reflection = parse_reflection_action(raw_action)
    action_succeeded = parsed_reflection.verdict == "succeeded"
    if lang == "en":
        task_finished = (
            "Task completed" in raw_action
            or "任务已完成" in raw_action
            or "finished" in raw_action.lower()
            or parsed_reflection.suggested_strategy == "finish"
        )
    else:
        task_finished = (
            "任务已完成" in raw_action
            or "Task completed" in raw_action
            or "finished" in raw_action.lower()
            or parsed_reflection.suggested_strategy == "finish"
        )

    reflection = response.thinking.strip()
    if not reflection:
        reflection = parsed_reflection.message
    reflection_fields = merge_verifier_with_reflection(
        verifier_result,
        {
            "action_succeeded": action_succeeded,
            "reflection_verdict": parsed_reflection.verdict,
            "failure_cause": parsed_reflection.failure_cause,
        },
    )
    action_succeeded = bool(reflection_fields["action_succeeded"])
    final_verdict = reflection_fields["reflection_verdict"]
    final_failure_cause = reflection_fields.get("failure_cause")
    retry_count = int(state.get("retry_count") or 0)
    if final_verdict in {"failed", "partial"}:
        retry_count += 1
    if verifier_result.hard_failure or final_verdict != "succeeded":
        task_finished = False

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
            summary=parsed_reflection.message,
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
            "verifier_result": verifier_result.to_dict(),
            "verifier_status": verifier_result.status,
            "verifier_failure_cause": verifier_result.failure_cause,
            "context_mode": context_mode,
            "context_truncated": context_updates.get("context_truncated", False),
            "failure_memory_hit_count": context_updates.get("failure_memory_hit_count", 0),
            "repeated_failure_count": context_updates.get("repeated_failure_count", 0),
        },
    )

    return {
        "screenshot_b64": screenshot.base64_data,
        "current_app": current_app,
        "reflection": reflection,
        "action_succeeded": action_succeeded,
        "reflection_verdict": final_verdict,
        "failure_cause": final_failure_cause,
        "suggested_strategy": parsed_reflection.suggested_strategy,
        "verifier_result": verifier_result.to_dict(),
        "verifier_status": verifier_result.status,
        "verifier_failure_cause": verifier_result.failure_cause,
        "retry_count": retry_count,
        "finished": task_finished,
        **context_updates,
    }
