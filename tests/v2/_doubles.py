"""Shared test doubles for v2 tools/resolver tests.

These fake the §6 ``PhoneSession`` surface and the DeviceFactory so the tools
layer can be unit-tested without a real device, MLX, or the (concurrently
built) ``phone_agent.v2.session``/``coords`` modules. Coordinate conversion is
implemented inline here (``x = int(rel / 1000 * w)``) to mirror v2.coords
semantics; integration wires the real converter.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from phone_agent.grounding.provider import MarkCandidate
from phone_agent.v2.resolver import LocateAmbiguousError, StaleMarkError


@dataclass
class FakeObservation:
    current_app: str
    marks: dict[str, MarkCandidate]
    screen_seq: int
    screenshot_b64: str = ""
    width: int = 1080
    height: int = 2400
    screen_hash: str = ""
    mime_type: str = "image/png"


class FakeDeviceFactory:
    """Records every device call for assertions; performs no real I/O.

    ``launch_result`` controls the bool ``launch_app`` returns (the tool must
    honor it — P0 #5). ``installed`` set to a frozenset enables the optional
    ``get_installed_app_inventory`` capability; left ``None`` the fake has no
    inventory (the tool then resolves without one, best-effort).
    """

    def __init__(self, launch_result: bool = True, installed: frozenset | None = None) -> None:
        self.calls: list[tuple] = []
        self.launched: list[str] = []
        self._launch_result = launch_result
        self._installed = installed

    def launch_app(self, app_name, device_id=None, delay=None, **kwargs):
        self.calls.append(("launch_app", app_name))
        if self._launch_result:
            self.launched.append(app_name)
        return self._launch_result

    def get_installed_app_inventory(self, device_id=None):
        from phone_agent.config.app_registry import InstalledAppInventory

        if self._installed is None:
            raise RuntimeError("inventory unavailable on this fake")
        return InstalledAppInventory(self._installed, device_id=device_id)

    def tap(self, x, y, device_id=None, delay=None):
        self.calls.append(("tap", x, y))

    def long_press(self, x, y, duration_ms=3000, device_id=None, delay=None):
        self.calls.append(("long_press", x, y))

    def swipe(self, sx, sy, ex, ey, duration_ms=None, device_id=None, delay=None):
        self.calls.append(("swipe", sx, sy, ex, ey))

    def back(self, device_id=None, delay=None):
        self.calls.append(("back",))

    def home(self, device_id=None, delay=None):
        self.calls.append(("home",))

    def type_text(self, text, device_id=None):
        self.calls.append(("type_text", text))

    def detect_and_set_adb_keyboard(self, device_id=None) -> str:
        self.calls.append(("detect_kbd",))
        return "com.original/.IME"

    def restore_keyboard(self, ime, device_id=None):
        self.calls.append(("restore_kbd", ime))


@dataclass
class FakeConfig:
    device_id: str | None = None


class FakePhoneSession:
    """Duck-typed §6 session used by resolver/tool tests."""

    def __init__(
        self,
        marks: dict[str, MarkCandidate] | None = None,
        *,
        width: int = 1080,
        height: int = 2400,
        current_app: str = "com.example.app",
        locate_result: MarkCandidate | None = None,
        locate_error: Exception | None = None,
        device_factory: FakeDeviceFactory | None = None,
        screenshot_b64: str = "QUJD",
        static_screen: bool = False,
    ) -> None:
        self.config = FakeConfig()
        self.device_factory = device_factory or FakeDeviceFactory()
        self.marks: dict[str, MarkCandidate] = dict(marks or {})
        self.screen_seq = 0
        self.screen_width = width
        self.screen_height = height
        self.current_app = current_app
        self.finished = False
        self.finish_summary: str | None = None
        self.takeover_reason: str | None = None
        # finish two-step review state (S2 §1.2): mirrors PhoneSession so the
        # control-tool tests exercise the real review/confirm seq guard.
        self.last_tool_ok: bool | None = None
        self.finish_reviewed: bool = False
        self.finish_review_seq: int = -1
        self.finish_dispute_count: int = 0
        self.finish_hard_doubts: list[str] = []
        self._locate_result = locate_result
        self._locate_error = locate_error
        self.observe_count = 0
        self._observe_should_fail = False
        # screenshot payload: a static screen keeps the same b64 across observes
        # (drives image-dedup tests); otherwise each observe bumps the payload.
        self._screenshot_b64 = screenshot_b64
        self._static_screen = static_screen

    # --- §6 surface -----------------------------------------------------
    def resolve_mark(self, mark_id: str) -> MarkCandidate:
        if mark_id not in self.marks:
            raise StaleMarkError(mark_id)
        return self.marks[mark_id]

    def mark_center_abs(self, mark: MarkCandidate) -> tuple[int, int]:
        cx, cy = mark.center
        return (
            int(cx / 1000 * self.screen_width),
            int(cy / 1000 * self.screen_height),
        )

    def relative_to_abs(self, rx: int, ry: int) -> tuple[int, int]:
        return (
            int(rx / 1000 * self.screen_width),
            int(ry / 1000 * self.screen_height),
        )

    def locate(self, description: str) -> MarkCandidate:
        if self._locate_error is not None:
            raise self._locate_error
        if self._locate_result is None:
            raise LocateAmbiguousError(f"no candidate for {description!r}")
        self.marks[self._locate_result.mark_id] = self._locate_result
        return self._locate_result

    def observe(self) -> FakeObservation:
        self.observe_count += 1
        if self._observe_should_fail:
            raise RuntimeError("boom")
        self.screen_seq += 1
        # Static screen -> constant payload; dynamic -> per-observe payload.
        b64 = (
            self._screenshot_b64
            if self._static_screen
            else f"{self._screenshot_b64}{self.screen_seq}"
        )
        screen_hash = hashlib.sha256(b64.encode("utf-8")).hexdigest()[:16]
        return FakeObservation(
            current_app=self.current_app,
            marks=self.marks,
            screen_seq=self.screen_seq,
            screenshot_b64=b64,
            width=self.screen_width,
            height=self.screen_height,
            screen_hash=screen_hash,
            mime_type="image/png",
        )


def make_mark(
    mark_id: str,
    *,
    text: str | None = None,
    role: str | None = None,
    center: tuple[int, int] = (500, 300),
) -> MarkCandidate:
    return MarkCandidate(
        mark_id=mark_id,
        bbox=[center[0] - 20, center[1] - 20, center[0] + 20, center[1] + 20],
        center=[center[0], center[1]],
        role=role,
        text_summary=text,
        source="fake",
    )
