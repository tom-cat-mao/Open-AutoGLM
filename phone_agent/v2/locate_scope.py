"""Scoped-locate crop geometry for the v2 visual ``locate`` tool.

The crop is an internal provider input only.  Its boxes are mapped back to the
full-frame 0-1000 coordinate space before a mark is minted, so actuation keeps
the normal marks-first / full-screen binding contract.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Sequence

from PIL import Image

from phone_agent.grounding.provider import MarkCandidate


@dataclass(frozen=True)
class ScopedImage:
    """Original-resolution crop in the screenshot shape providers accept."""

    base64_data: str
    width: int
    height: int
    mime_type: str = "image/png"


@dataclass(frozen=True)
class ScopeCrop:
    """Crop geometry plus exact crop-local -> full-frame affine mapping."""

    origin_1000: tuple[float, float]
    size_1000: tuple[float, float]
    crop: ScopedImage

    @property
    def bbox_1000(self) -> tuple[float, float, float, float]:
        ox, oy = self.origin_1000
        sx, sy = self.size_1000
        return (ox, oy, ox + sx, oy + sy)

    def map_box_to_full(
        self, box: Sequence[float]
    ) -> tuple[float, float, float, float]:
        ox, oy = self.origin_1000
        sx, sy = self.size_1000
        bx1, by1, bx2, by2 = (float(value) for value in box)
        return (
            ox + bx1 * sx / 1000.0,
            oy + by1 * sy / 1000.0,
            ox + bx2 * sx / 1000.0,
            oy + by2 * sy / 1000.0,
        )

    def map_point_to_full(self, point: Sequence[float]) -> tuple[float, float]:
        ox, oy = self.origin_1000
        sx, sy = self.size_1000
        x, y = (float(value) for value in point)
        return (ox + x * sx / 1000.0, oy + y * sy / 1000.0)


_CONTAINER_ROLE_NAMES = frozenset(
    {
        "view",
        "listview",
        "scrollview",
        "gridview",
        "recyclerview",
        "viewpager",
        "viewgroup",
        "abslistview",
        "stackview",
        "framelayout",
        "linearlayout",
        "relativelayout",
        "constraintlayout",
        "coordinatelayout",
        "nestedscrollview",
        "cardview",
        "gridlayout",
        "容器",
        "列表",
        "网格",
        "卡片",
    }
)


def is_container_like(mark: MarkCandidate) -> bool:
    """Return whether a mark role explicitly denotes a container."""

    return str(mark.role or "").casefold() in _CONTAINER_ROLE_NAMES


def interval_region_1000(
    start: MarkCandidate, end: MarkCandidate | None
) -> tuple[float, float, float, float]:
    """Build ``[start.top, end.top)`` with container or full-screen width."""

    if is_container_like(start):
        x1, x2 = float(start.bbox[0]), float(start.bbox[2])
    else:
        x1, x2 = 0.0, 1000.0
    y1 = float(start.bbox[1])
    y2 = float(end.bbox[1]) if end is not None else float(start.bbox[3])
    return (x1, y1, x2, y2)


def build_scope_crop(
    screenshot: Any,
    *,
    session: Any,
    region_bbox_1000: Sequence[float],
    padding_ratio: float,
) -> ScopeCrop:
    """Crop a relative scope from the original frame or raise ``ValueError``.

    Relative-to-pixel conversion deliberately goes through the session's
    canonical ``relative_to_abs`` tool boundary.  Crop edges are clamped to the
    decoded frame; no invalid scope ever falls back to the full screenshot.
    """

    raw = getattr(screenshot, "base64_data", None)
    width_px = int(getattr(session, "screen_width", 0) or 0)
    height_px = int(getattr(session, "screen_height", 0) or 0)
    if not raw or width_px <= 0 or height_px <= 0:
        raise ValueError("scope crop requires a valid screenshot and screen size")

    sx1, sy1, sx2, sy2 = (float(value) for value in region_bbox_1000)
    if sx2 <= sx1 or sy2 <= sy1:
        raise ValueError("scope region is inverted or degenerate")

    try:
        image = Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")
    except Exception as exc:
        raise ValueError("scope screenshot cannot be decoded") from exc
    image_w, image_h = image.size
    if image_w <= 0 or image_h <= 0:
        raise ValueError("scope screenshot has invalid dimensions")

    # V2's coordinate boundary owns 0-1000 -> absolute-pixel conversion.
    px1, py1 = session.relative_to_abs(round(sx1), round(sy1))
    px2, py2 = session.relative_to_abs(round(sx2), round(sy2))
    pad_x = max(0.0, (px2 - px1) * padding_ratio)
    pad_y = max(0.0, (py2 - py1) * padding_ratio)
    cx1 = int(max(0.0, min(px1 - pad_x, image_w - 1)))
    cy1 = int(max(0.0, min(py1 - pad_y, image_h - 1)))
    cx2 = int(min(image_w, px2 + pad_x))
    cy2 = int(min(image_h, py2 + pad_y))
    if cx2 <= cx1 or cy2 <= cy1:
        raise ValueError("scope crop is empty after clamping")

    region = image.crop((cx1, cy1, cx2, cy2))
    buffered = BytesIO()
    region.save(buffered, format="PNG")
    return ScopeCrop(
        origin_1000=(cx1 / width_px * 1000.0, cy1 / height_px * 1000.0),
        size_1000=(
            (cx2 - cx1) / width_px * 1000.0,
            (cy2 - cy1) / height_px * 1000.0,
        ),
        crop=ScopedImage(
            base64_data=base64.b64encode(buffered.getvalue()).decode("ascii"),
            width=cx2 - cx1,
            height=cy2 - cy1,
        ),
    )


# Historical names retained as the explicit geometry seam for focused tests
# and code-history comparison.
_build_scope_crop = build_scope_crop
_interval_region_1000 = interval_region_1000
_is_container_like = is_container_like


__all__ = [
    "ScopeCrop",
    "ScopedImage",
    "_build_scope_crop",
    "_interval_region_1000",
    "_is_container_like",
    "build_scope_crop",
    "interval_region_1000",
    "is_container_like",
]
