"""Tool dispatch: maps action names to @tool functions, provides unified dispatch.

Replaces ActionHandler._get_handler() hardcoded dict with @tool functions
that can be used by LangGraph ToolNode or called directly.
"""

from typing import Any

from phone_agent.actions.result import ActionResult

from phone_agent.graph.tools.tap import tap
from phone_agent.graph.tools.type_text import type_text
from phone_agent.graph.tools.swipe import swipe
from phone_agent.graph.tools.navigation import back, home
from phone_agent.graph.tools.launch import launch
from phone_agent.graph.tools.press import double_tap, long_press
from phone_agent.graph.tools.wait import wait
from phone_agent.graph.tools.misc import note, call_api, interact
from phone_agent.graph.tools.runtime import (
    reset_tool_device_factory,
    set_tool_device_factory,
)


def get_tool_map() -> dict[str, Any]:
    """Get mapping from action names to @tool functions.

    Returns:
        Dict mapping action name strings to tool functions.
    """
    return {
        "Tap": tap,
        "Type": type_text,
        "Type_Name": type_text,
        "Swipe": swipe,
        "Back": back,
        "Home": home,
        "Launch": launch,
        "Double Tap": double_tap,
        "Long Press": long_press,
        "Wait": wait,
        "Note": note,
        "Call_API": call_api,
        "Interact": interact,
    }


def get_all_tools() -> list:
    """Get list of all @tool functions for ToolNode registration.

    Returns:
        List of tool functions (deduplicated, Type_Name maps to same as Type).
    """
    seen = set()
    tools = []
    for tool_fn in get_tool_map().values():
        if tool_fn.name not in seen:
            seen.add(tool_fn.name)
            tools.append(tool_fn)
    return tools


def dispatch_tool(
    action: dict[str, Any],
    screen_width: int,
    screen_height: int,
    device_id: str | None = None,
    device_factory: Any | None = None,
) -> ActionResult:
    """Dispatch an action dict to the appropriate @tool function.

    This replaces ActionHandler.execute() for the graph path.
    Handles both "do" and "finish" metadata.

    Args:
        action: Parsed action dict with _metadata key.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.
        device_id: Optional ADB device ID.
        device_factory: Optional DeviceFactory injected from StateGraph config.

    Returns:
        ActionResult from tool execution.

    Raises:
        ValueError: If action type is unknown.
    """
    action_type = action.get("_metadata")

    if action_type == "finish":
        return ActionResult(
            success=True, should_finish=True, message=action.get("message", "")
        )

    # F1: Locate is dispatched by the execute-node internal capability branch
    # (it needs state + config, which dispatch_tool cannot see). A stray call
    # arriving here — or an un-grounded ``intent`` action that still names
    # Locate — must not fall into the unknown-type terminal branch: return a
    # no-device, no-finish result so the graph routes back to plan instead of
    # killing the run.
    if action_name := action.get("action"):
        if str(action_name) == "Locate":
            return ActionResult(
                success=True,
                should_finish=False,
                message="Locate handled by internal capability dispatch",
            )
    if action_type == "intent" and str(action.get("action") or "") == "Locate":
        return ActionResult(
            success=True,
            should_finish=False,
            message="Locate handled by internal capability dispatch",
        )

    if action_type != "do":
        return ActionResult(
            success=False,
            should_finish=True,
            message=f"Unknown action type: {action_type}",
        )

    action_name: str | None = action.get("action")
    tool_map = get_tool_map()
    if action_name is None:
        return ActionResult(
            success=False,
            should_finish=False,
            message="Missing action name in action dict",
        )
    tool_fn = tool_map.get(action_name)

    if tool_fn is None:
        return ActionResult(
            success=False,
            should_finish=False,
            message=f"Unknown action: {action_name}",
        )

    # Build kwargs from action dict, excluding metadata keys
    kwargs = {k: v for k, v in action.items() if k not in ("_metadata", "action")}

    # Get the underlying Python function from StructuredTool for signature inspection
    import inspect

    raw_func = tool_fn.func if hasattr(tool_fn, "func") else tool_fn
    sig = inspect.signature(raw_func)
    valid_params = set(sig.parameters.keys())

    call_kwargs = {}
    for k, v in kwargs.items():
        if k in valid_params:
            call_kwargs[k] = v

    # Add screen dimensions if the tool accepts them
    if "screen_width" in valid_params:
        call_kwargs["screen_width"] = screen_width
    if "screen_height" in valid_params:
        call_kwargs["screen_height"] = screen_height
    if "device_id" in valid_params:
        call_kwargs["device_id"] = device_id

    # Invoke the underlying function directly (bypassing StructuredTool.invoke overhead).
    # DeviceFactory is injected via a runtime context so it never appears in the
    # model-visible @tool schema.
    token = set_tool_device_factory(device_factory)
    try:
        result_dict = raw_func(**call_kwargs)
    finally:
        reset_tool_device_factory(token)

    # Tool functions return ActionResult.__dict__, convert back
    if isinstance(result_dict, dict):
        # F7: add-only metadata passthrough — tools may attach machine keys
        # (e.g. launch_resolved_package) that must survive dispatch into
        # action_result for the reflect step. The ``metadata`` key passes
        # through verbatim; any other non-canonical keys are collected under
        # ``metadata``.
        metadata = result_dict.get("metadata")
        extra = {
            key: value
            for key, value in result_dict.items()
            if key
            not in (
                "success",
                "should_finish",
                "message",
                "requires_confirmation",
                "metadata",
            )
        }
        return ActionResult(
            success=result_dict.get("success", True),
            should_finish=result_dict.get("should_finish", False),
            message=result_dict.get("message"),
            requires_confirmation=bool(
                result_dict.get("requires_confirmation", False)
            ),
            metadata=metadata if isinstance(metadata, dict) else (extra or None),
        )
    return result_dict
