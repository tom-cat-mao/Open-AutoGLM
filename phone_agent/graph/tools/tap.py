"""Tap tool: tap on screen at relative coordinates."""

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult
from phone_agent.graph.tools.coords import convert_relative_to_absolute


@tool
def tap(
    element: list[int],
    screen_width: int,
    screen_height: int,
    device_id: str | None = None,
    message: str | None = None,
) -> dict:
    """Tap on screen at the given relative coordinates (0-1000).

    If 'message' is provided, this is a sensitive operation requiring confirmation.
    The caller (execute_node) should route to confirm_node before invoking this tool.

    Args:
        element: Relative coordinates [x, y] in 0-1000 range.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.
        device_id: Optional ADB device ID.
        message: If present, this tap requires user confirmation.

    Returns:
        ActionResult serialized as dict.
    """
    from phone_agent.device_factory import get_device_factory

    x, y = convert_relative_to_absolute(element, screen_width, screen_height)
    device_factory = get_device_factory()
    device_factory.tap(x, y, device_id)
    return ActionResult(success=True, should_finish=False).__dict__
