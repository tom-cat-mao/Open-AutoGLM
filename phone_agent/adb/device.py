"""Device control utilities for Android automation."""

import re
import subprocess
import time
from typing import Any, Iterable

from phone_agent.config.app_registry import (
    ForegroundAppObservation,
    InstalledAppInventory,
)
from phone_agent.config.apps import DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_TARGET_RESOLVER
from phone_agent.config.timing import TIMING_CONFIG
from phone_agent.grounding.accessibility import parse_uiautomator_marks


def get_current_app(device_id: str | None = None) -> str:
    """
    Get the currently focused app name.

    Args:
        device_id: Optional ADB device ID for multi-device setups.

    Returns:
        Canonical display name when recognized, otherwise the observed package.
    """
    return get_foreground_app(device_id).display_name


def get_foreground_app(device_id: str | None = None) -> ForegroundAppObservation:
    """Return the observed foreground package/activity without guessing."""

    component = get_focused_window_or_app(device_id)
    if not component:
        raise ValueError("No focused package/activity")
    return DEFAULT_APP_REGISTRY.foreground_observation(component)


def get_focused_window_or_app(device_id: str | None = None) -> str | None:
    """Return the focused package/activity component for verifier diagnostics."""

    output = _run_adb_shell_text(device_id, ["dumpsys", "window"], timeout=5)
    if not output:
        return None
    for line in output.splitlines():
        if "mCurrentFocus" in line:
            component = _extract_component(line)
            if component:
                return component
    for line in output.splitlines():
        if "mFocusedApp" in line:
            component = _extract_component(line)
            if component:
                return component
    return None


def get_top_activity(device_id: str | None = None) -> str | None:
    """Return the current focused package/activity when available."""

    focused = get_focused_window_or_app(device_id)
    if not focused:
        return None
    return focused


def get_installed_app_inventory(device_id: str | None = None) -> InstalledAppInventory:
    """Return the installed Android package inventory for launch diagnostics."""

    output = _run_adb_shell_text(device_id, ["pm", "list", "packages"], timeout=10)
    packages = {
        line.removeprefix("package:").strip()
        for line in output.splitlines()
        if line.startswith("package:") and line.removeprefix("package:").strip()
    }
    return InstalledAppInventory(frozenset(packages), device_id=device_id)


def is_keyboard_visible(device_id: str | None = None) -> bool:
    """Return whether Android reports the soft keyboard/IME as visible."""

    output = _run_adb_shell_text(device_id, ["dumpsys", "input_method"], timeout=5)
    if not output:
        return False
    truthy_patterns = (
        r"\bmInputShown=true\b",
        r"\bmWindowVisible=true\b",
    )
    return any(re.search(pattern, output) for pattern in truthy_patterns)


def tap(
    x: int, y: int, device_id: str | None = None, delay: float | None = None
) -> None:
    """
    Tap at the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        device_id: Optional ADB device ID.
        delay: Delay in seconds after tap. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_tap_delay

    adb_prefix = _get_adb_prefix(device_id)

    subprocess.run(
        adb_prefix + ["shell", "input", "tap", str(x), str(y)], capture_output=True
    )
    time.sleep(delay)


def double_tap(
    x: int, y: int, device_id: str | None = None, delay: float | None = None
) -> None:
    """
    Double tap at the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        device_id: Optional ADB device ID.
        delay: Delay in seconds after double tap. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_double_tap_delay

    adb_prefix = _get_adb_prefix(device_id)

    subprocess.run(
        adb_prefix + ["shell", "input", "tap", str(x), str(y)], capture_output=True
    )
    time.sleep(TIMING_CONFIG.device.double_tap_interval)
    subprocess.run(
        adb_prefix + ["shell", "input", "tap", str(x), str(y)], capture_output=True
    )
    time.sleep(delay)


def long_press(
    x: int,
    y: int,
    duration_ms: int = 3000,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    """
    Long press at the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        duration_ms: Duration of press in milliseconds.
        device_id: Optional ADB device ID.
        delay: Delay in seconds after long press. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_long_press_delay

    adb_prefix = _get_adb_prefix(device_id)

    subprocess.run(
        adb_prefix
        + ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)],
        capture_output=True,
    )
    time.sleep(delay)


def swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: int | None = None,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    """
    Swipe from start to end coordinates.

    Args:
        start_x: Starting X coordinate.
        start_y: Starting Y coordinate.
        end_x: Ending X coordinate.
        end_y: Ending Y coordinate.
        duration_ms: Duration of swipe in milliseconds (auto-calculated if None).
        device_id: Optional ADB device ID.
        delay: Delay in seconds after swipe. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_swipe_delay

    adb_prefix = _get_adb_prefix(device_id)

    if duration_ms is None:
        # Calculate duration based on distance
        dist_sq = (start_x - end_x) ** 2 + (start_y - end_y) ** 2
        duration_ms = int(dist_sq / 1000)
        duration_ms = max(1000, min(duration_ms, 2000))  # Clamp between 1000-2000ms

    subprocess.run(
        adb_prefix
        + [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration_ms),
        ],
        capture_output=True,
    )
    time.sleep(delay)


def back(device_id: str | None = None, delay: float | None = None) -> None:
    """
    Press the back button.

    Args:
        device_id: Optional ADB device ID.
        delay: Delay in seconds after pressing back. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_back_delay

    adb_prefix = _get_adb_prefix(device_id)

    subprocess.run(
        adb_prefix + ["shell", "input", "keyevent", "4"], capture_output=True
    )
    time.sleep(delay)


def home(device_id: str | None = None, delay: float | None = None) -> None:
    """
    Press the home button.

    Args:
        device_id: Optional ADB device ID.
        delay: Delay in seconds after pressing home. If None, uses configured default.
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_home_delay

    adb_prefix = _get_adb_prefix(device_id)

    result = subprocess.run(
        adb_prefix + ["shell", "input", "keyevent", "KEYCODE_HOME"], capture_output=True
    )
    if result.returncode != 0 or _has_inject_events_error(result):
        subprocess.run(
            adb_prefix
            + [
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.HOME",
            ],
            capture_output=True,
            text=True,
        )
    time.sleep(delay)


def launch_app(
    app_name: str,
    device_id: str | None = None,
    delay: float | None = None,
    *,
    package_candidates: Iterable[str] | None = None,
    learning: Any | None = None,
    inventory: InstalledAppInventory | None = None,
) -> bool:
    """
    Launch an app by name, package name, or candidate package hints.

    Resolution chain: static registry alias -> per-run learned mapping ->
    device inventory substring match against ``package_candidates``. The
    device is the fact source: an app installed on the device is launchable.

    Args:
        app_name: The app name (static name, package name, or user term).
        device_id: Optional ADB device ID.
        delay: Delay in seconds after launching. If None, uses configured default.
        package_candidates: Optional candidate package names/keywords for apps
            not present in the static registry.
        learning: Optional per-run learned app term -> package mapping.
        inventory: Optional pre-fetched installed inventory; fetched when None.

    Returns:
        True if app was launched, False otherwise (resolution failed, app not
        installed, or launch error).
    """
    if delay is None:
        delay = TIMING_CONFIG.device.default_launch_delay

    if inventory is None:
        inventory = get_installed_app_inventory(device_id)
    target = DEFAULT_LAUNCH_TARGET_RESOLVER.resolve(
        app_name,
        inventory=inventory,
        candidates=package_candidates,
        learning=learning,
    )
    if target.status != "resolved" or not target.package_name:
        return False

    adb_prefix = _get_adb_prefix(device_id)
    package = target.package_name

    component = _resolve_launcher_component(adb_prefix, package)
    if component:
        result = subprocess.run(
            adb_prefix + ["shell", "am", "start", "-n", component],
            capture_output=True,
            text=True,
        )
    else:
        result = subprocess.run(
            adb_prefix
            + [
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                "-p",
                package,
            ],
            capture_output=True,
            text=True,
        )

    time.sleep(delay)
    return result.returncode == 0 and "Error:" not in (result.stdout + result.stderr)


def dump_uiautomator_xml(
    device_id: str | None = None, timeout: float | None = None
) -> str:
    """Return the current Android UiAutomator hierarchy XML from stdout."""

    adb_prefix = _get_adb_prefix(device_id)
    try:
        result = subprocess.run(
            adb_prefix + ["exec-out", "uiautomator", "dump", "/dev/tty"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("UiAutomator dump timed out") from exc
    output = result.stdout or ""
    marker = "<?xml"
    start = output.find(marker)
    end = output.find("</hierarchy>", start)
    if result.returncode != 0 or start < 0:
        raise ValueError("No UiAutomator XML output")
    if end < 0:
        raise ValueError("Incomplete UiAutomator XML output")
    return output[start : end + len("</hierarchy>")].strip()


def get_screen_marks(
    device_id: str | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    timeout: float | None = None,
    max_marks: int = 80,
) -> list[dict]:
    """Return Accessibility/UiAutomator marks in normalized 0-1000 coordinates."""

    xml_text = dump_uiautomator_xml(device_id, timeout=timeout)
    if width is None or height is None:
        from phone_agent.adb.screenshot import get_screenshot

        screenshot = get_screenshot(device_id)
        width = int(getattr(screenshot, "width", 0) or 0)
        height = int(getattr(screenshot, "height", 0) or 0)
    return parse_uiautomator_marks(
        xml_text,
        screen_width=int(width or 0),
        screen_height=int(height or 0),
        source="uiautomator",
        max_marks=max_marks,
    )


def _resolve_launcher_component(adb_prefix: list[str], package: str) -> str | None:
    """Resolve a package's launcher activity component via package manager."""
    result = subprocess.run(
        adb_prefix
        + [
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            package,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    for line in reversed(result.stdout.splitlines()):
        component = line.strip()
        if not component or component.startswith("No activity found"):
            continue
        if "/" in component:
            return component
    return None


def _has_inject_events_error(result: subprocess.CompletedProcess) -> bool:
    """Return True if an adb command failed due to INJECT_EVENTS restrictions."""
    output = b""
    for stream in (result.stdout, result.stderr):
        if isinstance(stream, bytes):
            output += stream
        elif isinstance(stream, str):
            output += stream.encode("utf-8", errors="ignore")
    normalized = output.lower()
    return b"inject_events" in normalized or b"inject events" in normalized


def _run_adb_shell_text(
    device_id: str | None,
    args: list[str],
    *,
    timeout: float = 5,
) -> str:
    adb_prefix = _get_adb_prefix(device_id)
    try:
        result = subprocess.run(
            adb_prefix + ["shell", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _extract_component(line: str) -> str | None:
    if "null" in line.lower():
        return None
    match = re.search(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", line)
    if not match:
        return None
    return match.group(0)[:200]


def _get_adb_prefix(device_id: str | None) -> list:
    """Get ADB command prefix with optional device specifier."""
    if device_id:
        return ["adb", "-s", device_id]
    return ["adb"]
