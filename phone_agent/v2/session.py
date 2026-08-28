"""v2 PhoneSession: device-side run state, screenshots, marks, and locate.

One :class:`PhoneSession` holds the mutable device-side state for a single run.
Tools reach the device and the current-screen marks exclusively through it. All
device I/O goes through :class:`~phone_agent.device_factory.DeviceFactory`
(P0: no direct ADB), and marks come from the grounding provider stack.

See ``docs/refactor-thin-loop-v2.md`` §6 for the binding contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phone_agent.device_factory import DeviceFactory, get_device_factory
from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.factory import build_locate_provider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    ScreenBinding,
)
from phone_agent.v2.coords import convert_relative_to_absolute

if TYPE_CHECKING:
    from phone_agent.adb.screenshot import Screenshot
    from phone_agent.grounding.provider import MarkProvider
    from phone_agent.v2.config import V2Config
    from phone_agent.v2.taskdoc import TaskDoc


class ScreenshotError(RuntimeError):
    """Raised when the device screenshot is unavailable / invalid."""


class StaleMarkError(RuntimeError):
    """Raised when a requested mark_id is not in the current-screen marks."""


class LocateAmbiguousError(RuntimeError):
    """Raised when visual locate returns zero or multiple confident candidates."""

    def __init__(self, message: str, *, candidates: list[MarkCandidate] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass
class Observation:
    """Node-local observation snapshot; never serialized to trace/checkpoint."""

    screenshot_b64: str
    width: int
    height: int
    current_app: str
    marks: list[MarkCandidate]
    screen_seq: int
    # Short sha256 of the screenshot base64 payload; drives same-screen image
    # dedup in ``tools/_obs.py`` (only re-send the image when the screen changed).
    screen_hash: str = ""
    # Screenshot mime type (``image/png`` | ``image/jpeg``) for the data: URL.
    mime_type: str = "image/png"


class PhoneSession:
    """Device-side state for one run. Tools access device + marks through it."""

    def __init__(
        self,
        config: "V2Config",
        device_factory: DeviceFactory | None = None,
    ) -> None:
        self.config = config
        self.device_factory = device_factory or get_device_factory()
        self.marks: dict[str, MarkCandidate] = {}
        self.screen_seq: int = 0
        self.finished: bool = False
        self.finish_summary: str | None = None
        self.takeover_reason: str | None = None
        # Last screenshot hash whose image block was actually sent to the model.
        # Same-screen re-observations reuse the text OBS but drop the image
        # (see ``tools/_obs.py``); reset to None so the first frame always ships.
        self.last_image_hash: str | None = None
        # TaskDoc (task board) state: doc is harness-seeded at run start (agent.py);
        # seen_states tracks (current_app, screen_hash) tuples for stagnation
        # detection; nudged fires the stagnation hint at most once per run.
        self.task_doc: "TaskDoc | None" = None
        self.seen_states: set[tuple[str, str]] = set()
        self.nudged: bool = False
        # lazy visual locate provider (singleton per session)
        self._locate_provider: "MarkProvider | None" = None
        self._locate_provider_built: bool = False
        # last-known dimensions for coordinate conversion
        self._last_width: int = 0
        self._last_height: int = 0

    # -- device state -----------------------------------------------------

    def screenshot(self) -> "Screenshot":
        """Capture a device screenshot; raise ScreenshotError if invalid."""

        shot = self.device_factory.get_screenshot(self.config.device_id)
        if not getattr(shot, "is_valid", False):
            code = getattr(shot, "failure_code", None) or "screenshot_unavailable"
            raise ScreenshotError(f"screenshot invalid: {code}")
        self._last_width = int(shot.width)
        self._last_height = int(shot.height)
        return shot

    def refresh_marks(self) -> list[MarkCandidate]:
        """Produce current-screen accessibility marks; never raises.

        Failure (bad screenshot, provider error, empty dump) returns ``[]`` so the
        caller can fall back to visual locate or a bare screenshot round.
        """

        try:
            shot = self.screenshot()
        except ScreenshotError:
            return []
        try:
            binding = self._screen_binding(shot)
            provider = AccessibilityTreeProvider(
                dump_tree=self._dump_tree,
                max_marks=self.config.accessibility_max_marks,
            )
            result = provider.provide_marks(
                shot,
                binding,
                hints=None,
                timeout=self.config.accessibility_timeout,
            )
        except Exception:
            return []
        if not result.success:
            return list(result.marks)
        return list(result.marks)

    def observe(self) -> Observation:
        """Full observation: screenshot + marks + foreground; updates session state."""

        shot = self.screenshot()
        marks = self.refresh_marks()
        current_app = self._foreground_label()
        self.screen_seq += 1
        self.marks = {mark.mark_id: mark for mark in marks}
        # Record (current_app, screen_hash) for TaskDoc stagnation detection.
        # screen_hash is a short sha256 of the screenshot base64 payload.
        screen_hash = hashlib.sha256(
            (shot.base64_data or "").encode("utf-8")
        ).hexdigest()[:16]
        self.seen_states.add((current_app, screen_hash))
        return Observation(
            screenshot_b64=shot.base64_data,
            width=int(shot.width),
            height=int(shot.height),
            current_app=current_app,
            marks=marks,
            screen_seq=self.screen_seq,
            screen_hash=screen_hash,
            mime_type=getattr(shot, "mime_type", None) or "image/png",
        )

    # -- mark resolution --------------------------------------------------

    def resolve_mark(self, mark_id: str) -> MarkCandidate:
        """Return the current-screen mark for ``mark_id`` or raise StaleMarkError."""

        mark = self.marks.get(mark_id)
        if mark is None:
            raise StaleMarkError(
                f"mark {mark_id!r} is not on the current screen; re-observe first"
            )
        return mark

    def mark_center_abs(self, mark: MarkCandidate) -> tuple[int, int]:
        """Convert a mark's 0-1000 center to absolute device pixels."""

        width = self._last_width
        height = self._last_height
        if width <= 0 or height <= 0:
            shot = self.screenshot()
            width, height = int(shot.width), int(shot.height)
        return convert_relative_to_absolute(list(mark.center), width, height)

    def locate(self, description: str) -> MarkCandidate:
        """Visual deep-locate ``description`` -> one confident mark (fail-closed).

        Registers the resolved mark into ``self.marks`` and returns it. Zero or
        multiple confident candidates raise :class:`LocateAmbiguousError` with a
        candidate summary; nothing is registered and nothing executes.
        """

        provider = self._get_locate_provider()
        if provider is None:
            raise LocateAmbiguousError("visual locate provider is unavailable")
        shot = self.screenshot()
        binding = self._screen_binding(shot)
        hint = MarkProviderHint(text=description, source="tool", action="locate")
        result = provider.provide_marks(
            shot,
            binding,
            hints=[hint],
            timeout=self.config.accessibility_timeout,
        )
        executable = [mark for mark in result.marks if getattr(mark, "valid", True)]
        if not result.success or len(executable) == 0:
            raise LocateAmbiguousError(
                f"no confident match for {description!r}",
                candidates=list(result.candidates),
            )
        if len(executable) > 1:
            raise LocateAmbiguousError(
                f"ambiguous match for {description!r}: {len(executable)} candidates",
                candidates=executable,
            )
        mark = executable[0]
        self.marks[mark.mark_id] = mark
        return mark

    # -- marks digest -----------------------------------------------------

    @staticmethod
    def format_marks_digest(marks: list[MarkCandidate], max_items: int = 40) -> str:
        """One line per mark: ``mark_id | role | text(<=32) | center``."""

        lines: list[str] = []
        for mark in marks[:max_items]:
            role = (mark.role or "?")[:24]
            text = (mark.text_summary or "").replace("\n", " ").strip()
            if len(text) > 32:
                text = text[:32]
            center = tuple(mark.center) if mark.center else ()
            lines.append(f"{mark.mark_id} | {role} | {text} | {center}")
        if len(marks) > max_items:
            lines.append(f"... (+{len(marks) - max_items} more)")
        return "\n".join(lines)

    # -- internals --------------------------------------------------------

    def _dump_tree(self, timeout: float | None = None) -> str:
        return self.device_factory.dump_uiautomator_xml(
            self.config.device_id, timeout=timeout
        )

    def _foreground_label(self) -> str:
        try:
            foreground = self.device_factory.get_foreground_app(self.config.device_id)
        except Exception:
            return "unknown"
        return foreground.display_name or foreground.package_name or "unknown"

    def _screen_binding(self, shot: "Screenshot") -> ScreenBinding:
        raw_hash = hashlib.sha256(
            (shot.base64_data or "").encode("utf-8")
        ).hexdigest()[:16]
        return ScreenBinding(
            screen_id=f"screen_{self.screen_seq}",
            raw_screenshot_hash=raw_hash,
            width=int(shot.width),
            height=int(shot.height),
            current_app=None,
        )

    def _get_locate_provider(self) -> "MarkProvider | None":
        if not self._locate_provider_built:
            cfg = {
                "grounding_provider_name": self.config.grounding_provider,
                "locateanything_max_size": self.config.locateanything_max_size,
            }
            if self.config.locateanything_model:
                cfg["grounding_model_path"] = self.config.locateanything_model
            self._locate_provider = build_locate_provider(cfg)
            self._locate_provider_built = True
        return self._locate_provider
