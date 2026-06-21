"""Type tool: input text on device."""

import time

from langchain_core.tools import tool

from phone_agent.actions.result import ActionResult
from phone_agent.config.timing import TIMING_CONFIG
from phone_agent.graph.tools.runtime import get_tool_device_factory


@tool
def type_text(
    text: str,
    device_id: str | None = None,
) -> dict:
    """Type text into the current text field on the device.

    Switches to ADB keyboard, clears existing text, types new text,
    then restores the original keyboard.

    Args:
        text: The text to type.
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    device_factory = get_tool_device_factory()

    # Switch to ADB keyboard
    original_ime = device_factory.detect_and_set_adb_keyboard(device_id)
    time.sleep(TIMING_CONFIG.action.keyboard_switch_delay)

    # Clear existing text and type new text
    device_factory.clear_text(device_id)
    time.sleep(TIMING_CONFIG.action.text_clear_delay)

    device_factory.type_text(text, device_id)
    time.sleep(TIMING_CONFIG.action.text_input_delay)

    # Restore original keyboard
    device_factory.restore_keyboard(original_ime, device_id)
    time.sleep(TIMING_CONFIG.action.keyboard_restore_delay)

    return ActionResult(success=True, should_finish=False).__dict__
