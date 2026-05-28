"""Wait tool: wait for a specified duration."""

import time

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult


@tool
def wait(duration: str = "1 seconds") -> dict:
    """Wait for a specified duration.

    Args:
        duration: Duration string like "1 seconds" or "2 seconds".

    Returns:
        ActionResult serialized as dict.
    """
    try:
        seconds = float(duration.replace("seconds", "").strip())
    except ValueError:
        seconds = 1.0

    time.sleep(seconds)
    return ActionResult(success=True, should_finish=False).__dict__
