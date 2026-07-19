"""Unavailable/delegated tool shims that fail closed if called directly."""

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
    return ActionResult(
        success=False, should_finish=False, message="Capability unavailable: Note"
    ).__dict__


@tool
def call_api(message: str = "") -> dict:
    """Call an external API for summarization (stub, no device action).

    Args:
        message: API call description.

    Returns:
        ActionResult serialized as dict.
    """
    return ActionResult(
        success=False, should_finish=False, message="Capability unavailable: Call_API"
    ).__dict__


@tool
def interact(message: str = "") -> dict:
    """Signal that user interaction is required (stub, no device action).

    Args:
        message: Interaction description.

    Returns:
        ActionResult serialized as dict.
    """
    return ActionResult(
        success=False, should_finish=False, message="User takeover required"
    ).__dict__
