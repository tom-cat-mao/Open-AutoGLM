"""Device control utilities for Android automation."""

import hashlib
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterable

from phone_agent.config.app_registry import (
    ForegroundAppObservation,
    InstalledAppInventory,
)
from phone_agent.config.apps import DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_TARGET_RESOLVER
from phone_agent.config.timing import TIMING_CONFIG
from phone_agent.grounding.accessibility import parse_uiautomator_marks


@dataclass(frozen=True)
class AppLabelEntry:
    """A launchable Android package and its user-visible application label."""

    package: str
    label: str


_APP_LABEL_CACHE: dict[
    tuple[str | None, str], tuple[AppLabelEntry, ...]
] = {}
_PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+")
_COMPONENT_RE = re.compile(
    r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+"
)


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


def get_serial_number(device_id: str | None = None) -> str | None:
    """Return the device serial via ``adb get-serialno`` (None when unavailable).

    Used to namespace per-device state (e.g. App-KB scopes) when the caller did
    not configure an explicit serial (the single-device default).
    """

    adb_prefix = _get_adb_prefix(device_id)
    try:
        result = subprocess.run(
            adb_prefix + ["get-serialno"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    serial = (result.stdout or "").strip()
    if not serial or serial.lower() in {"unknown", "unauthorized"}:
        return None
    return serial


def get_app_labels(
    device_id: str | None = None, *, timeout: float = 30.0
) -> list[AppLabelEntry]:
    """Return launchable packages with localized device application labels.

    Label lookup uses one remote shell loop and is cached until the observed package
    set changes. Some OEM ROMs (e.g. ColorOS) strip ``pm get-application-label``:
    when the label loop is unavailable/fails we degrade to *package-as-label*
    entries (the caller still learns what is installed and launchable). Only when
    no launchable package can be listed at all do we return an empty result.
    """

    try:
        packages = _get_launchable_packages(device_id, timeout=timeout)
        if not packages:
            return []

        sorted_packages = sorted(packages)
        package_digest = hashlib.sha256(
            "\n".join(sorted_packages).encode("utf-8")
        ).hexdigest()
        cache_key = (device_id, package_digest)
        cached = _APP_LABEL_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)

        package_words = " ".join(shlex.quote(package) for package in sorted_packages)
        script = (
            f"for pkg in {package_words}; do "
            'if ! label="$(pm get-application-label \"$pkg\")"; '
            "then exit 1; fi; "
            "printf '%s\\t%s\\n' \"$pkg\" \"$label\"; "
            "done"
        )
        output = _run_adb_shell_text(device_id, [script], timeout=timeout)
        entries = _parse_app_label_output(output, packages) if output else None
        if entries is None:
            # Label command unavailable/stripped (ColorOS, …) or garbled output:
            # degrade to package-as-label so callers still get the install facts.
            entries = [
                AppLabelEntry(package=package, label=package)
                for package in sorted_packages
            ]
        _APP_LABEL_CACHE[cache_key] = tuple(entries)
        return entries
    except Exception:
        return []


def _get_launchable_packages(
    device_id: str | None, *, timeout: float
) -> set[str]:
    """Query launcher activities, falling back to third-party packages."""

    intent_args = [
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    ]
    launcher_queries = (
        ["cmd", "package", "query-activities", "--brief", *intent_args],
        ["pm", "query-activities", "--brief", *intent_args],
        ["pm", "query-activities", *intent_args, "--brief"],
    )
    for query in launcher_queries:
        output = _run_adb_shell_text(device_id, query, timeout=timeout)
        packages = _parse_component_packages(output)
        if packages:
            return packages

    output = _run_adb_shell_text(
        device_id, ["pm", "list", "packages", "-3"], timeout=timeout
    )
    return _parse_package_list(output)


def _parse_component_packages(output: str) -> set[str]:
    """Extract bare package names from package/activity component lines."""

    return {match.group(1) for match in _COMPONENT_RE.finditer(output)}


def _parse_package_list(output: str) -> set[str]:
    """Parse ``pm list packages`` output without accepting diagnostics."""

    packages: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("package:"):
            continue
        package = stripped.removeprefix("package:").strip()
        if _PACKAGE_NAME_RE.fullmatch(package):
            packages.add(package)
    return packages


def _parse_app_label_output(
    output: str, expected_packages: set[str]
) -> list[AppLabelEntry] | None:
    """Parse one label-loop response, rejecting incomplete or foreign output."""

    entries: list[AppLabelEntry] = []
    seen: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        package, separator, label = line.partition("\t")
        package = package.strip()
        if not separator or package not in expected_packages or package in seen:
            return None
        seen.add(package)
        label = label.strip()
        if label:
            entries.append(AppLabelEntry(package=package, label=label))
    if seen != expected_packages:
        return None
    return entries


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
    device_id: str | None = None,
    timeout: float | None = None,
    *,
    windowed: str = "auto",
) -> str:
    """Return the current Android UiAutomator hierarchy XML from stdout.

    WP-G2a windowed dual-mode (``windowed`` = ``auto`` | ``on`` | ``off``):

    * ``off`` — legacy ``uiautomator dump /dev/tty`` (single ``<hierarchy>`` root).
    * ``on``  — ``uiautomator dump --windows /dev/tty``; a device that does not
      emit a ``<displays>`` root raises a visible error (no silent fallback).
    * ``auto`` (default) — try ``--windows`` first; if the device does not
      support it (timeout / no XML / no ``<displays>`` root) fall back to the
      legacy dump so the caller always gets a usable tree.

    The ``<?xml … </root>`` truncation now recognizes both roots: ``--windows``
    output is ``<displays>…</displays>`` (with nested ``<hierarchy>`` blocks per
    window), while the legacy dump is a single ``<hierarchy>…</hierarchy>``.
    """

    mode = str(windowed or "auto").strip().lower()
    if mode not in {"auto", "on", "off"}:
        mode = "auto"
    adb_prefix = _get_adb_prefix(device_id)

    if mode in {"auto", "on"}:
        payload: str | None = None
        try:
            raw = _run_uiautomator_dump(adb_prefix, windows=True, timeout=timeout)
            payload = _extract_uiautomator_payload(raw)
        except (TimeoutError, ValueError):
            payload = None
        if payload and "<displays" in payload:
            return payload
        if mode == "on":
            raise ValueError(
                "UiAutomator --windows dump unavailable on this device"
            )
        # auto: --windows produced a usable non-windowed tree — reuse it rather
        # than paying for a second dump.
        if payload:
            return payload
        # auto: --windows failed outright — fall through to the legacy dump.

    return _extract_uiautomator_payload(
        _run_uiautomator_dump(adb_prefix, windows=False, timeout=timeout)
    )


def _run_uiautomator_dump(
    adb_prefix: list[str], *, windows: bool, timeout: float | None
) -> subprocess.CompletedProcess:
    """Run one ``uiautomator dump`` invocation; raise TimeoutError on timeout."""

    command = adb_prefix + ["exec-out", "uiautomator", "dump"]
    if windows:
        command.append("--windows")
    command.append("/dev/tty")
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("UiAutomator dump timed out") from exc


def _extract_uiautomator_payload(result: subprocess.CompletedProcess) -> str:
    """Slice ``<?xml … </root>`` from stdout for the ``<displays>``/``<hierarchy>`` root."""

    output = result.stdout or ""
    marker = "<?xml"
    start = output.find(marker)
    if result.returncode != 0 or start < 0:
        raise ValueError("No UiAutomator XML output")
    close_tag = _root_closing_tag(output, start)
    end = output.rfind(close_tag)
    if end < start:
        raise ValueError("Incomplete UiAutomator XML output")
    return output[start : end + len(close_tag)].strip()


def _root_closing_tag(output: str, start: int) -> str:
    """Pick ``</displays>`` when the root is a windows dump, else ``</hierarchy>``."""

    displays = output.find("<displays", start)
    hierarchy = output.find("<hierarchy", start)
    if displays != -1 and (hierarchy == -1 or displays < hierarchy):
        return "</displays>"
    return "</hierarchy>"



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
