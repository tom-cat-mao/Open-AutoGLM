"""Navigation tools: back and home button actions."""

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult
from phone_agent.graph.tools.runtime import get_tool_device_factory


@tool
def back(device_id: str | None = None) -> dict:
    """Press the back button on the device.

    Args:
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    device_factory = get_tool_device_factory()
    device_factory.back(device_id)
    return ActionResult(success=True, should_finish=False).__dict__


@tool
def home(device_id: str | None = None) -> dict:
    """Press the home button on the device.

    Args:
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    device_factory = get_tool_device_factory()
    device_factory.home(device_id)
    return ActionResult(success=True, should_finish=False).__dict__
