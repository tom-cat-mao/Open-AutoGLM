"""Runtime-only dependency injection helpers for graph tools.

The public ``@tool`` function signatures are model-visible schema. Keep side-effect
dependencies such as ``DeviceFactory`` out of those signatures and inject them via
``dispatch_tool()`` at execution time.
"""

from contextvars import ContextVar, Token
from typing import Any

_device_factory_var: ContextVar[Any | None] = ContextVar(
    "phone_agent_tool_device_factory", default=None
)


def set_tool_device_factory(device_factory: Any | None) -> Token[Any | None]:
    """Set the DeviceFactory for the current tool-dispatch context."""
    return _device_factory_var.set(device_factory)


def reset_tool_device_factory(token: Token[Any | None]) -> None:
    """Reset the DeviceFactory context to its previous value."""
    _device_factory_var.reset(token)


def get_tool_device_factory() -> Any:
    """Get the injected DeviceFactory, falling back for direct tool invocation."""
    device_factory = _device_factory_var.get()
    if device_factory is not None:
        return device_factory

    from phone_agent.device_factory import get_device_factory

    return get_device_factory()
