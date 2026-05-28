"""Launch tool: launch an app by name."""

from langchain_core.tools import tool

from phone_agent.actions.handler import ActionResult


@tool
def launch(app: str, device_id: str | None = None) -> dict:
    """Launch an app by its display name.

    Looks up the app name in the APP_PACKAGES mapping to find the
    Android package name, then launches it via ADB.

    Args:
        app: The display name of the app (e.g. "微信", "Chrome").
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    from phone_agent.device_factory import get_device_factory

    device_factory = get_device_factory()
    success = device_factory.launch_app(app, device_id)
    if success:
        return ActionResult(success=True, should_finish=False).__dict__
    return ActionResult(success=False, should_finish=False, message=f"App not found: {app}").__dict__
