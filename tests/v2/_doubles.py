"""Shared test doubles for v2 tools/resolver tests.

These fake the §6 ``PhoneSession`` surface and the DeviceFactory so the tools
layer can be unit-tested without a real device, MLX, or the (concurrently
built) ``phone_agent.v2.session``/``coords`` modules. Coordinate conversion is
implemented inline here (``x = int(rel / 1000 * w)``) to mirror v2.coords
semantics; integration wires the real converter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phone_agent.grounding.provider import MarkCandidate
from phone_agent.v2.resolver import LocateAmbiguousError, StaleMarkError


@dataclass
class FakeObservation:
    current_app: str
    marks: dict[str, MarkCandidate]
    screen_seq: int


class FakeDeviceFactory:
    """Records every device call for assertions; performs no real I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.launched: list[str] = []

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

    def launch_app(self, app_name, device_id=None, delay=None, **kwargs):
        self.calls.append(("launch_app", app_name))
        self.launched.append(app_name)
        return True


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
        self._locate_result = locate_result
        self._locate_error = locate_error
        self.observe_count = 0
        self._observe_should_fail = False

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
        return FakeObservation(
            current_app=self.current_app,
            marks=self.marks,
            screen_seq=self.screen_seq,
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
