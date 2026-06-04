"""Observation builder for screen-bound harness metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phone_agent.graph.marks import (
    MarkRegistry,
    build_mark_topology_digest,
    build_screen_id,
    build_semantic_screen_id,
    compute_perceptual_hash,
    compute_raw_screenshot_hash,
)


@dataclass(frozen=True)
class ScreenSnapshot:
    screen_id: str
    screen_hash: str
    current_app: str
    width: int
    height: int
    semantic_screen_id: str
    observation_epoch: int
    mark_set_version: str | None
    perceptual_hash: str
    raw_screenshot_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "screen_hash": self.screen_hash,
            "current_app": self.current_app,
            "width": self.width,
            "height": self.height,
            "semantic_screen_id": self.semantic_screen_id,
            "observation_epoch": self.observation_epoch,
            "mark_set_version": self.mark_set_version,
            "perceptual_hash": self.perceptual_hash,
            "raw_screenshot_hash": self.raw_screenshot_hash,
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

    width = int(getattr(screenshot, "width", 0) or 0)
    height = int(getattr(screenshot, "height", 0) or 0)
    screenshot_b64 = getattr(screenshot, "base64_data", None)
    screen_id = build_screen_id(
        current_app=current_app,
        screenshot_b64=screenshot_b64,
        width=width,
        height=height,
        marks=marks,
    )
    semantic_screen_id = build_semantic_screen_id(current_app=current_app, width=width, height=height)
    mark_topology_digest = build_mark_topology_digest(marks)
    perceptual_hash = compute_perceptual_hash(
        screenshot_b64,
        fallback_key=f"{semantic_screen_id}|{mark_topology_digest}",
    )
    raw_screenshot_hash = compute_raw_screenshot_hash(screenshot_b64)
    registry = MarkRegistry.from_marks(screen_id, marks)
    registry = MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id=semantic_screen_id,
        observation_epoch=0,
        mark_set_version=registry.mark_set_version or mark_topology_digest,
        perceptual_hash=perceptual_hash,
        raw_screenshot_hash=raw_screenshot_hash,
    )
    snapshot = ScreenSnapshot(
        screen_id=screen_id,
        screen_hash=raw_screenshot_hash,
        current_app=current_app,
        width=width,
        height=height,
        semantic_screen_id=semantic_screen_id,
        observation_epoch=0,
        mark_set_version=registry.mark_set_version,
        perceptual_hash=perceptual_hash,
        raw_screenshot_hash=raw_screenshot_hash,
    )
    return Observation(snapshot=snapshot, mark_registry=registry)
