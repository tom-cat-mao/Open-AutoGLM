"""Launch tool: launch an app by name, package name, or candidate hints.

The device is the fact source: an installed app is launchable even when the
static registry has never heard of it. Resolution order (see
``LaunchTargetResolver.resolve``): static registry alias -> per-run learned
mapping -> device inventory substring match against ``package_candidates``.
A successful launch records the mapping (user term -> package) in the per-run
learning context so later verifier/plan steps and launch calls reuse it.
"""

from langchain_core.tools import tool

from phone_agent.actions.result import ActionResult
from phone_agent.config.apps import DEFAULT_LAUNCH_TARGET_RESOLVER
from phone_agent.graph.tools.runtime import (
    get_tool_app_learning,
    get_tool_device_factory,
    get_tool_trace_emitter,
)

UNKNOWN_LAUNCH_MESSAGE_CN = "未在设备找到该应用，可从桌面图标启动"


def _launch_decision_payload(target, term: str, candidates) -> dict:
    """Structured add-only launch-decision trace payload."""

    payload: dict = {
        "term": term,
        "status": target.status,
        "package_name": target.package_name,
    }
    if target.candidates:
        payload["candidates"] = [str(item) for item in target.candidates]
    if candidates:
        payload["requested_candidates"] = [str(item) for item in candidates]
    return payload


@tool
def launch(
    app: str,
    package_candidates: list[str] | None = None,
    device_id: str | None = None,
) -> dict:
    """Launch an app by its display name, package name, or candidate hints.

    Resolution order: static registry alias -> per-run learned mapping ->
    device inventory substring match against ``package_candidates``. When the
    app is not installed and cannot be resolved, no launch is attempted; use
    the home-screen icon path instead.

    Args:
        app: The display name of the app (e.g. "微信", "Chrome").
        package_candidates: Optional candidate package names or keywords for
            apps not present in the static registry (e.g. ["com.tongcheng.android"]).
        device_id: Optional ADB device ID.

    Returns:
        ActionResult serialized as dict.
    """
    device_factory = get_tool_device_factory()
    learning = get_tool_app_learning()
    trace_emitter = get_tool_trace_emitter()
    inventory = device_factory.get_installed_app_inventory(device_id)
    target = DEFAULT_LAUNCH_TARGET_RESOLVER.resolve(
        app,
        inventory=inventory,
        candidates=package_candidates,
        learning=learning,
    )
    if trace_emitter is not None:
        trace_emitter(
            "launch_decision", _launch_decision_payload(target, app, package_candidates)
        )
    if target.status != "resolved" or not target.package_name:
        if target.status == "ambiguous":
            message = (
                f"{app}: multiple candidate packages match "
                f"({', '.join(str(item) for item in target.candidates)}); "
                "give an explicit package name or use the home-screen icon"
            )
        else:
            message = f"{app}: {UNKNOWN_LAUNCH_MESSAGE_CN}"
        return ActionResult(
            success=False, should_finish=False, message=message
        ).__dict__
    success = device_factory.launch_app(
        app,
        device_id,
        package_candidates=package_candidates,
        learning=learning,
    )
    if success:
        if learning is not None:
            learning.record(app, target.package_name)
        return ActionResult(success=True, should_finish=False).__dict__
    return ActionResult(
        success=False, should_finish=False, message=f"App launch failed: {app}"
    ).__dict__
