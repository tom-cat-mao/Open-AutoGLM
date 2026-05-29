"""Plan node: screenshot → build messages → model inference → parse action."""

import traceback
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.actions.handler import parse_action, finish
from phone_agent.config import get_system_prompt
from phone_agent.model.client import MessageBuilder

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


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

    # 2. Build new messages (only the new ones, reducer will append)
    new_messages = []
    if step_count == 0:
        system_prompt = configurable.get("system_prompt") or get_system_prompt(lang)
        new_messages.append(MessageBuilder.create_system_message(system_prompt))

        screen_info = MessageBuilder.build_screen_info(current_app)
        text_content = f"{task}\n\n{screen_info}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content, image_base64=screenshot.base64_data
            )
        )
    else:
        screen_info = MessageBuilder.build_screen_info(current_app)
        # Include previous reflection if available
        reflection = state.get("reflection")
        if reflection:
            text_content = f"** Screen Info **\n\n{screen_info}\n\n** Reflection **\n\n{reflection}"
        else:
            text_content = f"** Screen Info **\n\n{screen_info}"
        new_messages.append(
            MessageBuilder.create_user_message(
                text=text_content, image_base64=screenshot.base64_data
            )
        )

    # 3. Model inference (pass full messages for context)
    full_messages = list(state["messages"]) + new_messages
    try:
        response = model_client.request(full_messages)
    except Exception as e:
        if configurable.get("verbose", True):
            traceback.print_exc()
        return {
            "messages": new_messages,
            "step_count": step_count + 1,
            "screenshot_b64": screenshot.base64_data,
            "current_app": current_app,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "thinking": "",
            "action_raw": "",
            "action_parsed": finish(message=f"Model error: {e}"),
            "error": f"Model error: {e}",
            "finished": True,
            "action_confirmed": False,
        }

    # 4. Parse action
    try:
        action_parsed = parse_action(response.action)
    except ValueError:
        action_parsed = finish(message=response.action)

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
    }
