"""Compatibility boundary for stable device screenshot/app sampling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phone_agent.config.app_registry import ForegroundAppObservation


@dataclass(frozen=True)
class GraphDeviceCapture:
    """Current screenshot and foreground facts used by one graph node."""

    screenshot: Any
    current_app: str
    foreground: ForegroundAppObservation | None
    observation_epoch: int


def capture_device_observation(
    device_factory: Any,
    device_id: str | None,
    *,
    timeout: int = 10,
    max_attempts: int = 2,
) -> GraphDeviceCapture:
    """Use composite sampling when supported, with a legacy test-double fallback."""

    if hasattr(device_factory, "capture_observation"):
        captured = device_factory.capture_observation(
            device_id,
            timeout=timeout,
            max_attempts=max_attempts,
        )
        return GraphDeviceCapture(
            screenshot=captured.screenshot,
            current_app=captured.foreground.display_name,
            foreground=captured.foreground,
            observation_epoch=captured.observation_epoch,
        )

    screenshot = device_factory.get_screenshot(device_id)
    return GraphDeviceCapture(
        screenshot=screenshot,
        current_app=device_factory.get_current_app(device_id),
        foreground=None,
        observation_epoch=0,
    )
