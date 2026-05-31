"""Press tools: double tap and long press actions."""

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult
from phone_agent.graph.tools.coords import convert_relative_to_absolute
from phone_agent.graph.tools.runtime import get_tool_device_factory


@tool
def double_tap(
    element: list[int],
    screen_width: int,
    screen_height: int,
    device_id: str | None = None,
) -> dict:
    """Double tap on screen at the given relative coordinates.

    Args:
        element: Relative coordinates [x, y] in 0-1000 range.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    x, y = convert_relative_to_absolute(element, screen_width, screen_height)
    device_factory = get_tool_device_factory()
    device_factory.double_tap(x, y, device_id)
    return ActionResult(success=True, should_finish=False).__dict__


@tool
def long_press(
    element: list[int],
    screen_width: int,
    screen_height: int,
    device_id: str | None = None,
) -> dict:
    """Long press on screen at the given relative coordinates.

    Args:
        element: Relative coordinates [x, y] in 0-1000 range.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    x, y = convert_relative_to_absolute(element, screen_width, screen_height)
    device_factory = get_tool_device_factory()
    device_factory.long_press(x, y, device_id=device_id)
    return ActionResult(success=True, should_finish=False).__dict__
