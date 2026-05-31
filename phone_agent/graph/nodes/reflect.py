"""Reflect node: screenshot → structured action outcome reflection."""

import ast
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.graph.trace import emit_trace
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT_CN = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效，并给出下一步建议。

你必须严格按照要求输出以下格式：
<think>{think}</think>
<answer>{action}</answer>

其中：
- {think} 是你的推理过程。
- {action} 必须优先使用：reflection(verdict="succeeded|failed|partial", failure_cause="none|element_not_found|wrong_page|app_not_responding|network_or_loading|unsafe_or_sensitive|unknown", suggested_strategy="continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish", message="xxx")
- 兼容旧格式：continue(message="xxx") 表示动作生效；retry(message="xxx") 表示动作未生效

判断标准：
1. 动作生效：页面如预期发生了变化（如点击后跳转、输入后文本出现、滑动后内容变化）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 部分成功：页面有变化但任务尚未完全进入预期状态
4. 任务完成：如果当前页面显示任务已经完成，输出 reflection(verdict="succeeded", failure_cause="none", suggested_strategy="finish", message="任务已完成")
"""

REFLECT_SYSTEM_PROMPT_EN = """You are a mobile automation reflection expert. Your job is to observe the screenshot after an action and judge whether the action succeeded, then give next-step advice.

You MUST strictly output in the following format:
<think>{think}</think>
<answer>{action}</answer>

Where:
- {think} is your reasoning process.
- {action} should use: reflection(verdict="succeeded|failed|partial", failure_cause="none|element_not_found|wrong_page|app_not_responding|network_or_loading|unsafe_or_sensitive|unknown", suggested_strategy="continue|retry|retry_with_offset|go_back|swipe_to_find|wait|takeover|finish", message="xxx")
- Legacy compatible formats are accepted: continue(message="xxx") or retry(message="xxx")

Judgment criteria:
1. Action succeeded: the page changed as expected (e.g., navigated after tap, text appeared after input, content changed after swipe)
2. Action failed: the page did not change, or the change was unexpected
3. Partial success: the page changed but is not yet in the expected state
4. Task completed: if the current page shows the task is done, output reflection(verdict="succeeded", failure_cause="none", suggested_strategy="finish", message="Task completed")
"""

VALID_VERDICTS = {"succeeded", "failed", "partial"}
VALID_FAILURE_CAUSES = {
    "none",
    "element_not_found",
    "wrong_page",
    "app_not_responding",
    "network_or_loading",
    "unsafe_or_sensitive",
    "unknown",
}
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
        if failure_cause not in VALID_FAILURE_CAUSES:
            failure_cause = "unknown"
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

    # 1. Capture screen again
    screenshot = device_factory.get_screenshot(device_id)
    current_app = device_factory.get_current_app(device_id)
    emit_trace(
        config,
        state,
        "reflect",
        "reflect_start",
        {"current_app": current_app, "action": action_parsed, "action_result": action_result},
    )

    # 2. Build reflection prompt with language selection
    if lang == "en":
        system_prompt = REFLECT_SYSTEM_PROMPT_EN
    else:
        system_prompt = REFLECT_SYSTEM_PROMPT_CN

    action_str = str(action_parsed) if action_parsed else "None"
    result_str = str(action_result) if action_result else "None"

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
            text=reflect_text, image_base64=screenshot.base64_data
        ),
    ]

    # 3. Model inference for reflection
    try:
        response = model_client.request(reflect_messages)
    except Exception as e:
        if verbose:
            traceback.print_exc()
        emit_trace(config, state, "reflect", "reflect_error", {"message": str(e)})
        return {
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "reflection": f"Reflection failed: {e}",
            "action_succeeded": True,  # Assume succeeded on error to avoid deadlock
            "reflection_verdict": "succeeded",
            "failure_cause": None,
            "suggested_strategy": "continue",
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
    retry_count = int(state.get("retry_count") or 0)
    if parsed_reflection.verdict in {"failed", "partial"}:
        retry_count += 1

    emit_trace(
        config,
        state,
        "reflect",
        "reflect_result",
        {
            "reflection": reflection,
            "action_raw": raw_action,
            "reflection_verdict": parsed_reflection.verdict,
            "failure_cause": parsed_reflection.failure_cause,
            "suggested_strategy": parsed_reflection.suggested_strategy,
            "action_succeeded": action_succeeded,
            "finished": task_finished,
        },
    )

    return {
        "screenshot_b64": screenshot.base64_data,
        "current_app": current_app,
        "reflection": reflection,
        "action_succeeded": action_succeeded,
        "reflection_verdict": parsed_reflection.verdict,
        "failure_cause": parsed_reflection.failure_cause,
        "suggested_strategy": parsed_reflection.suggested_strategy,
        "retry_count": retry_count,
        "finished": task_finished,
    }
