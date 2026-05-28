"""Reflect node: screenshot → judge if action succeeded → output reflection."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


REFLECT_SYSTEM_PROMPT = """你是一个手机自动化任务的反思专家。你的职责是观察动作执行后的屏幕截图，判断动作是否生效，并给出下一步建议。

你必须严格按照要求输出以下格式：
<think>{think}</think>
<answer>{action}</answer>

其中：
- {think} 是你的推理过程。
- {action} 必须是以下两种之一：
  - continue(message="xxx") 表示动作生效，继续任务
  - retry(message="xxx") 表示动作未生效，需要重试或调整策略

判断标准：
1. 动作生效：页面如预期发生了变化（如点击后跳转、输入后文本出现、滑动后内容变化）
2. 动作未生效：页面没有变化，或变化与预期不符
3. 任务完成：如果当前页面显示任务已经完成，输出 continue(message="任务已完成")
"""


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

    action_parsed = state.get("action_parsed")
    action_result = state.get("action_result")
    task = state["task"]
    step_count = state["step_count"]
    max_steps = state["max_steps"]

    # 1. Capture screen again
    screenshot = device_factory.get_screenshot(device_id)
    current_app = device_factory.get_current_app(device_id)

    # 2. Build reflection prompt
    action_str = str(action_parsed) if action_parsed else "None"
    result_str = str(action_result) if action_result else "None"

    screen_info = MessageBuilder.build_screen_info(current_app)
    reflect_text = (
        f"原始任务：{task}\n"
        f"当前步数：{step_count} / {max_steps}\n"
        f"刚执行的动作：{action_str}\n"
        f"执行结果：{result_str}\n"
        f"当前屏幕信息：{screen_info}\n\n"
        f"请观察当前截图，判断动作是否生效，并给出下一步建议。"
    )

    reflect_messages = [
        MessageBuilder.create_system_message(REFLECT_SYSTEM_PROMPT),
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
        return {
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "reflection": f"Reflection failed: {e}",
            "action_succeeded": True,  # Assume succeeded on error to avoid deadlock
        }

    # 4. Parse reflection
    raw_action = response.action.strip()
    action_succeeded = raw_action.startswith("continue")
    task_finished = "任务已完成" in raw_action or "finished" in raw_action.lower()

    reflection = response.thinking.strip()
    if not reflection:
        reflection = raw_action

    return {
        "screenshot_b64": screenshot.base64_data,
        "current_app": current_app,
        "reflection": reflection,
        "action_succeeded": action_succeeded,
        "finished": task_finished,
    }
