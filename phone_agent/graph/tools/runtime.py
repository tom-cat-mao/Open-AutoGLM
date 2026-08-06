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

_app_learning_var: ContextVar[Any | None] = ContextVar(
    "phone_agent_tool_app_learning", default=None
)

_trace_emitter_var: ContextVar[Any | None] = ContextVar(
    "phone_agent_tool_trace_emitter", default=None
)


def set_tool_app_learning(learning: Any | None) -> Token[Any | None]:
    """Set the per-run learned app mapping for the current tool-dispatch context."""
    return _app_learning_var.set(learning)


def reset_tool_app_learning(token: Token[Any | None]) -> None:
    """Reset the learned app mapping context to its previous value."""
    _app_learning_var.reset(token)


def get_tool_app_learning() -> Any | None:
    """Get the per-run learned app mapping, or None outside a graph dispatch."""
    return _app_learning_var.get()


def set_tool_trace_emitter(emitter: Any | None) -> Token[Any | None]:
    """Set an ``(event, payload)`` trace emitter for the current dispatch context."""
    return _trace_emitter_var.set(emitter)


def reset_tool_trace_emitter(token: Token[Any | None]) -> None:
    """Reset the trace emitter context to its previous value."""
    _trace_emitter_var.reset(token)


def get_tool_trace_emitter() -> Any | None:
    """Get the trace emitter, or None outside a graph dispatch."""
    return _trace_emitter_var.get()



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
