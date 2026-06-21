"""Misc tools: note, call_api, interact — stub actions with no device side-effect."""

from langchain_core.tools import tool

from phone_agent.actions.result import ActionResult


@tool
def note(message: str = "") -> dict:
    """Record a note about the current page content (stub, no device action).

    Args:
        message: The note content.

    Returns:
        ActionResult serialized as dict.
    """
    return ActionResult(success=True, should_finish=False).__dict__


@tool
def call_api(message: str = "") -> dict:
    """Call an external API for summarization (stub, no device action).

    Args:
        message: API call description.

    Returns:
        ActionResult serialized as dict.
    """
    return ActionResult(success=True, should_finish=False).__dict__


@tool
def interact(message: str = "") -> dict:
    """Signal that user interaction is required (stub, no device action).

    Args:
        message: Interaction description.

    Returns:
        ActionResult serialized as dict.
    """
    return ActionResult(
        success=True, should_finish=False, message="User interaction required"
    ).__dict__
