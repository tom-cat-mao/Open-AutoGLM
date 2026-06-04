"""Observation builder for screen-bound harness metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phone_agent.graph.marks import MarkRegistry, build_screen_id


@dataclass(frozen=True)
class ScreenSnapshot:
    screen_id: str
    screen_hash: str
    current_app: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "screen_hash": self.screen_hash,
            "current_app": self.current_app,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class Observation:
    snapshot: ScreenSnapshot
    mark_registry: MarkRegistry

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot.to_dict(), "mark_registry": self.mark_registry.to_dict()}


def build_observation(
    *, screenshot: Any, current_app: str, marks: list[dict[str, Any]] | None = None
) -> Observation:
    """Build a screen observation with optional mock/provider marks.

    Provider fallback is intentionally safe: without marks, only screen id/hash
    are produced and mark-based actions cannot ground.
    """

    screen_id = build_screen_id(
        current_app=current_app,
        screenshot_b64=getattr(screenshot, "base64_data", None),
        width=int(getattr(screenshot, "width", 0) or 0),
        height=int(getattr(screenshot, "height", 0) or 0),
    )
    snapshot = ScreenSnapshot(
        screen_id=screen_id,
        screen_hash=screen_id,
        current_app=current_app,
        width=int(getattr(screenshot, "width", 0) or 0),
        height=int(getattr(screenshot, "height", 0) or 0),
    )
    return Observation(snapshot=snapshot, mark_registry=MarkRegistry.from_marks(screen_id, marks))
