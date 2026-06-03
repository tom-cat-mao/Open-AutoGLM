"""Screenshot utilities for capturing Android device screen."""

import base64
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Tuple

from PIL import Image


@dataclass
class Screenshot:
    """Represents a captured screenshot."""

    base64_data: str
    width: int
    height: int
    mime_type: str = "image/png"
    is_sensitive: bool = False


def get_screenshot(device_id: str | None = None, timeout: int = 10) -> Screenshot:
    """
    Capture a screenshot from the connected Android device.

    Args:
        device_id: Optional ADB device ID for multi-device setups.
        timeout: Timeout in seconds for screenshot operations.

    Returns:
        Screenshot object containing base64 data and dimensions.

    Note:
        If the screenshot fails (e.g., on sensitive screens like payment pages),
        a black fallback image is returned with is_sensitive=True.
    """
    temp_path = os.path.join(tempfile.gettempdir(), f"screenshot_{uuid.uuid4()}.png")
    device_temp_path = f"/sdcard/tmp_{uuid.uuid4().hex}.png"
    adb_prefix = _get_adb_prefix(device_id)

    try:
        # Execute screenshot command
        result = subprocess.run(
            adb_prefix + ["shell", "screencap", "-p", device_temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # Check for screenshot failure (sensitive screen)
        output = result.stdout + result.stderr
        if "Status: -1" in output or "Failed" in output:
            return _create_fallback_screenshot(is_sensitive=True)

        # Pull screenshot to local temp path
        subprocess.run(
            adb_prefix + ["pull", device_temp_path, temp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if not os.path.exists(temp_path):
            return _create_fallback_screenshot(is_sensitive=False)

        # Read and encode image
        img = Image.open(temp_path)
        width, height = img.size

        buffered = BytesIO()
        mime_type = _save_model_image(img, buffered)
        base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

        return Screenshot(
            base64_data=base64_data,
            width=width,
            height=height,
            mime_type=mime_type,
            is_sensitive=False,
        )

    except Exception as e:
        print(f"Screenshot error: {e}")
        return _create_fallback_screenshot(is_sensitive=False)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        try:
            subprocess.run(
                adb_prefix + ["shell", "rm", "-f", device_temp_path],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            pass


def _get_adb_prefix(device_id: str | None) -> list:
    """Get ADB command prefix with optional device specifier."""
    if device_id:
        return ["adb", "-s", device_id]
    return ["adb"]


def _save_model_image(img: Image.Image, buffered: BytesIO) -> str:
    """Save screenshot payload for model input while preserving screen dimensions."""
    image_format = os.getenv("PHONE_AGENT_SCREENSHOT_FORMAT", "jpeg").lower()
    if image_format in {"jpg", "jpeg"}:
        quality = _parse_jpeg_quality()
        img.convert("RGB").save(
            buffered,
            format="JPEG",
            quality=max(1, min(95, quality)),
            optimize=True,
        )
        return "image/jpeg"

    img.save(buffered, format="PNG", optimize=True)
    return "image/png"


def _parse_jpeg_quality() -> int:
    """Parse JPEG quality with a safe default for malformed env values."""
    try:
        return max(1, min(95, int(os.getenv("PHONE_AGENT_SCREENSHOT_JPEG_QUALITY", "80"))))
    except ValueError:
        return 80


def _create_fallback_screenshot(is_sensitive: bool) -> Screenshot:
    """Create a black fallback image when screenshot fails."""
    default_width, default_height = 1080, 2400

    black_img = Image.new("RGB", (default_width, default_height), color="black")
    buffered = BytesIO()
    mime_type = _save_model_image(black_img, buffered)
    base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return Screenshot(
        base64_data=base64_data,
        width=default_width,
        height=default_height,
        mime_type=mime_type,
        is_sensitive=is_sensitive,
    )
