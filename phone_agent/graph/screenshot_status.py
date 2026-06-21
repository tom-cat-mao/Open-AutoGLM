"""Helpers for structured screenshot availability checks."""

from __future__ import annotations

from typing import Any


def screenshot_failure_code(screenshot: Any) -> str | None:
    """Return a stable failure code when a screenshot is unavailable or invalid."""

    if getattr(screenshot, "is_valid", True) is False:
        return str(getattr(screenshot, "failure_code", None) or "screenshot_unavailable")
    if not getattr(screenshot, "base64_data", None):
        return "screenshot_unavailable"
    if int(getattr(screenshot, "width", 0) or 0) <= 0 or int(getattr(screenshot, "height", 0) or 0) <= 0:
        return "screenshot_unavailable"
    return None


def screenshot_failure_message(screenshot: Any) -> str:
    """Return a short trace-safe screenshot failure message."""

    message = getattr(screenshot, "failure_message", None)
    if message:
        return str(message)
    code = screenshot_failure_code(screenshot) or "screenshot_unavailable"
    return code


def screenshot_is_sensitive(screenshot: Any) -> bool:
    """Return whether the screenshot failure came from a sensitive/secure screen."""

    return bool(getattr(screenshot, "is_sensitive", False))
