"""Device factory for Android ADB device control."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from phone_agent.config.app_registry import (
    ForegroundAppObservation,
    InstalledAppInventory,
)


@dataclass(frozen=True)
class CapturedDeviceObservation:
    """Stable foreground facts and screenshot captured in one sampling window."""

    screenshot: Any
    foreground: ForegroundAppObservation
    observation_epoch: int
    attempts: int


class ObservationCaptureError(RuntimeError):
    """Raised when a stable composite device observation cannot be captured."""

    def __init__(self, code: str, *, attempts: int) -> None:
        super().__init__(code)
        self.code = code
        self.attempts = attempts


class DeviceType(Enum):
    """Type of device connection tool."""

    ADB = "adb"


class DeviceFactory:
    """
    Factory class for getting device-specific implementations.

    This allows the system to work with Android (ADB) devices.
    """

    def __init__(self, device_type: DeviceType = DeviceType.ADB):
        """
        Initialize the device factory.

        Args:
            device_type: The type of device to use (ADB).
        """
        self.device_type = device_type
        self._module = None
        self._observation_epoch = 0

    @property
    def module(self):
        """Get the appropriate device module (adb)."""
        if self._module is None:
            if self.device_type == DeviceType.ADB:
                from phone_agent import adb

                self._module = adb
            else:
                raise ValueError(f"Unknown device type: {self.device_type}")
        return self._module

    def get_screenshot(self, device_id: str | None = None, timeout: int = 10):
        """Get screenshot from device."""
        return self.module.get_screenshot(device_id, timeout)

    def get_screen_marks(
        self,
        device_id: str | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        timeout: float | None = None,
        max_marks: int = 80,
    ) -> list[dict]:
        """Get normalized Accessibility/UiAutomator marks from the device."""
        if not hasattr(self.module, "get_screen_marks"):
            return []
        return self.module.get_screen_marks(
            device_id,
            width=width,
            height=height,
            timeout=timeout,
            max_marks=max_marks,
        )

    def dump_uiautomator_xml(
        self, device_id: str | None = None, timeout: float | None = None
    ) -> str:
        """Return the current UIAutomator hierarchy through the device boundary."""

        if not hasattr(self.module, "dump_uiautomator_xml"):
            raise RuntimeError("UIAutomator hierarchy is unavailable")
        return self.module.dump_uiautomator_xml(device_id, timeout=timeout)

    def get_current_app(self, device_id: str | None = None) -> str:
        """Get current app name."""
        return self.module.get_current_app(device_id)

    def get_foreground_app(
        self, device_id: str | None = None
    ) -> ForegroundAppObservation:
        """Get structured foreground package/activity facts."""

        if hasattr(self.module, "get_foreground_app"):
            return self.module.get_foreground_app(device_id)
        component = self.get_top_activity(device_id)
        if component:
            from phone_agent.config.apps import DEFAULT_APP_REGISTRY

            return DEFAULT_APP_REGISTRY.foreground_observation(component)
        raise ValueError("Foreground app observation is unavailable")

    def capture_observation(
        self,
        device_id: str | None = None,
        *,
        timeout: int = 10,
        max_attempts: int = 2,
    ) -> CapturedDeviceObservation:
        """Capture a screenshot bracketed by matching foreground observations."""

        attempts = max(1, int(max_attempts))
        for attempt in range(1, attempts + 1):
            before = self.get_foreground_app(device_id)
            screenshot = self.get_screenshot(device_id, timeout)
            after = self.get_foreground_app(device_id)
            if before.component_name and before.component_name == after.component_name:
                self._observation_epoch += 1
                return CapturedDeviceObservation(
                    screenshot=screenshot,
                    foreground=after,
                    observation_epoch=self._observation_epoch,
                    attempts=attempt,
                )
        raise ObservationCaptureError("observation_unstable", attempts=attempts)

    def get_installed_app_inventory(
        self, device_id: str | None = None
    ) -> InstalledAppInventory:
        """Return the device package inventory without granting launch authority."""

        if not hasattr(self.module, "get_installed_app_inventory"):
            return InstalledAppInventory(frozenset(), device_id=device_id)
        return self.module.get_installed_app_inventory(device_id)

    def get_focused_window_or_app(self, device_id: str | None = None) -> str | None:
        """Get the focused Android window/app diagnostic line."""
        if not hasattr(self.module, "get_focused_window_or_app"):
            return None
        return self.module.get_focused_window_or_app(device_id)

    def get_top_activity(self, device_id: str | None = None) -> str | None:
        """Get the focused package/activity component when available."""
        if not hasattr(self.module, "get_top_activity"):
            return None
        return self.module.get_top_activity(device_id)

    def is_keyboard_visible(self, device_id: str | None = None) -> bool:
        """Return whether the Android soft keyboard/IME is visible."""
        if not hasattr(self.module, "is_keyboard_visible"):
            return False
        return bool(self.module.is_keyboard_visible(device_id))

    def tap(
        self, x: int, y: int, device_id: str | None = None, delay: float | None = None
    ):
        """Tap at coordinates."""
        return self.module.tap(x, y, device_id, delay)

    def double_tap(
        self, x: int, y: int, device_id: str | None = None, delay: float | None = None
    ):
        """Double tap at coordinates."""
        return self.module.double_tap(x, y, device_id, delay)

    def long_press(
        self,
        x: int,
        y: int,
        duration_ms: int = 3000,
        device_id: str | None = None,
        delay: float | None = None,
    ):
        """Long press at coordinates."""
        return self.module.long_press(x, y, duration_ms, device_id, delay)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int | None = None,
        device_id: str | None = None,
        delay: float | None = None,
    ):
        """Swipe from start to end."""
        return self.module.swipe(
            start_x, start_y, end_x, end_y, duration_ms, device_id, delay
        )

    def back(self, device_id: str | None = None, delay: float | None = None):
        """Press back button."""
        return self.module.back(device_id, delay)

    def home(self, device_id: str | None = None, delay: float | None = None):
        """Press home button."""
        return self.module.home(device_id, delay)

    def launch_app(
        self,
        app_name: str,
        device_id: str | None = None,
        delay: float | None = None,
        *,
        package_candidates: Iterable[str] | None = None,
        learning: Any | None = None,
        inventory: InstalledAppInventory | None = None,
    ) -> bool:
        """Launch an app by name, package name, or candidate package hints."""
        return self.module.launch_app(
            app_name,
            device_id,
            delay,
            package_candidates=package_candidates,
            learning=learning,
            inventory=inventory,
        )

    def type_text(self, text: str, device_id: str | None = None):
        """Type text."""
        return self.module.type_text(text, device_id)

    def clear_text(self, device_id: str | None = None):
        """Clear text."""
        return self.module.clear_text(device_id)

    def detect_and_set_adb_keyboard(self, device_id: str | None = None) -> str:
        """Detect and set keyboard."""
        return self.module.detect_and_set_adb_keyboard(device_id)

    def restore_keyboard(self, ime: str, device_id: str | None = None):
        """Restore keyboard."""
        return self.module.restore_keyboard(ime, device_id)

    def list_devices(self):
        """List connected devices."""
        return self.module.list_devices()

    def get_connection_class(self):
        """Get the connection class (ADBConnection)."""
        from phone_agent.adb import ADBConnection

        return ADBConnection


# Global device factory instance
_device_factory: DeviceFactory | None = None


def set_device_type(device_type: DeviceType):
    """
    Set the global device type.

    Args:
        device_type: The device type to use (ADB).
    """
    global _device_factory
    _device_factory = DeviceFactory(device_type)


def get_device_factory() -> DeviceFactory:
    """
    Get the global device factory instance.

    Returns:
        The device factory instance.
    """
    global _device_factory
    if _device_factory is None:
        _device_factory = DeviceFactory(DeviceType.ADB)  # Default to ADB
    return _device_factory
