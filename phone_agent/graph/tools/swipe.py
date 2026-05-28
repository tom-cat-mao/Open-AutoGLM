"""Swipe tool: swipe on screen between two relative coordinates."""

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult
from phone_agent.graph.tools.coords import convert_relative_to_absolute


@tool
def swipe(
    start: list[int],
    end: list[int],
    screen_width: int,
    screen_height: int,
    device_id: str | None = None,
) -> dict:
    """Swipe from start to end coordinates on screen.

    Args:
        start: Relative start coordinates [x, y] in 0-1000 range.
        end: Relative end coordinates [x, y] in 0-1000 range.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    from phone_agent.device_factory import get_device_factory

    start_x, start_y = convert_relative_to_absolute(start, screen_width, screen_height)
    end_x, end_y = convert_relative_to_absolute(end, screen_width, screen_height)

    device_factory = get_device_factory()
    device_factory.swipe(start_x, start_y, end_x, end_y, device_id=device_id)
    return ActionResult(success=True, should_finish=False).__dict__
