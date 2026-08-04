"""S2: scoped locate — crop geometry, affine back-mapping, fail-closed paths.

The provider runs on the high-resolution crop of F; its crop-local 0-1000 box
is mapped back to full-screen 0-1000 before mark registration. The registered
mark still binds F's hash (P0 #9), and every scoped failure path is fail-closed
(never a silent full-frame fallback).
"""

import base64
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from phone_agent.actions.grounding import ground_intent_to_action
from phone_agent.graph.marks import (
    Mark,
    MarkRegistry,
    compute_raw_screenshot_hash,
)
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.tools.coords import convert_relative_to_absolute
from phone_agent.graph.tools.locate import (
    _build_scope_crop,
    locate_target,
)
from phone_agent.grounding.fake import FakeGroundingProvider


_SCREEN = "screen-1"


def _png_b64(width: int, height: int, color=(200, 30, 30)) -> str:
    image = Image.new("RGB", (width, height), color)
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("ascii")


class _PNGDevice:
    """Minimal device stub returning a real PNG screenshot."""

    def __init__(self, payload: str, width: int, height: int) -> None:
        self.payload = payload
        self.width = width
        self.height = height
        self.calls: list[tuple] = []

    def get_screenshot(self, device_id=None):
        self.calls.append(("get_screenshot", (device_id,), {}))
        return type(
            "Shot",
            (),
            {
                "base64_data": self.payload,
                "width": self.width,
                "height": self.height,
                "mime_type": "image/png",
            },
        )()

    def tap(self, x: int, y: int, device_id=None) -> None:
        self.calls.append(("tap", (x, y, device_id), {}))


class _RecordingProvider(FakeGroundingProvider):
    """Fake provider that records the exact image it was asked to inspect."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_size: tuple[int, int] | None = None
        self.received_payload: str | None = None

    def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
        self.received_size = (
            getattr(screenshot, "width", None),
            getattr(screenshot, "height", None),
        )
        self.received_payload = getattr(screenshot, "base64_data", None)
        return super().provide_marks(screenshot, screen_binding, hints, timeout)


def _registry(*, scope_bbox, raw_hash: str) -> MarkRegistry:
    return MarkRegistry(
        screen_id=_SCREEN,
        marks={
            "ax_1": Mark(
                mark_id="ax_1",
                screen_id=_SCREEN,
                bbox=scope_bbox,
                center=((scope_bbox[0] + scope_bbox[2]) / 2, (scope_bbox[1] + scope_bbox[3]) / 2),
                source="accessibility",
                role="View",
                text_summary="容器",
            )
        },
        semantic_screen_id="semantic-1",
        observation_epoch=1,
        raw_screenshot_hash=raw_hash,
    )


def _locate_state(base_state, *, png_b64: str, width: int, height: int, scope_bbox, scope_mark_id: str | None) -> dict:
    state = dict(base_state)
    raw_hash = compute_raw_screenshot_hash(png_b64)
    state["screen_width"] = width
    state["screen_height"] = height
    state["mark_registry"] = _registry(scope_bbox=scope_bbox, raw_hash=raw_hash).to_dict()
    state["locate_count"] = 0
    action: dict = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月2日",
    }
    if scope_mark_id is not None:
        action["scope_mark_id"] = scope_mark_id
    state["action_parsed"] = action
    state["action_raw"] = '{"type":"intent","action":"locate","target_text_hint":"10月2日"}'
    return state


def _config(provider, device) -> dict:
    return {"configurable": {"device_factory": device, "locate_provider": provider}}


# ----------------------------------------------------------------------
# Crop geometry + affine back-mapping (pure)
# ----------------------------------------------------------------------


def test_scope_crop_geometry_and_back_mapping_exact() -> None:
    """Known crop + box → exact full-frame 0-1000 values (round-trip)."""
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    scope_mark = Mark(
        mark_id="ax_1",
        screen_id=_SCREEN,
        bbox=(200, 200, 400, 400),  # px (400,400,800,800)
        center=(300, 300),
        source="accessibility",
    )
    scope = _build_scope_crop(
        type("Shot", (), {"base64_data": png})(),
        scope_mark=scope_mark,
        width_px=frame_w,
        height_px=frame_h,
    )
    assert scope is not None
    # pad = 5% of box extent per side: 20px; crop (380,380,820,820) = 440x440.
    assert scope.crop.width == 440
    assert scope.crop.height == 440
    assert scope.origin_1000 == (190.0, 190.0)
    assert scope.size_1000 == (220.0, 220.0)
    # LA box [100,100,200,200] in crop space → full = origin + box*size/1000.
    mapped = scope.map_box_to_full((100, 100, 200, 200))
    assert mapped == pytest.approx((212.0, 212.0, 234.0, 234.0))
    # Full-frame round trip: the crop's own corners map back to the padded region.
    assert scope.map_box_to_full((0, 0, 1000, 1000)) == pytest.approx(
        (190.0, 190.0, 410.0, 410.0)
    )


def test_scope_padding_clamps_to_frame_edges() -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)

    def _shot():
        return type("Shot", (), {"base64_data": png})()

    # Top-left corner: padding expands beyond the frame → clamped to 0.
    tl = Mark(
        mark_id="ax_1", screen_id=_SCREEN, bbox=(0, 0, 100, 100), center=(50, 50),
        source="accessibility",
    )
    tl_scope = _build_scope_crop(_shot(), scope_mark=tl, width_px=frame_w, height_px=frame_h)
    assert tl_scope is not None
    assert (tl_scope.crop.width, tl_scope.crop.height) == (210, 210)
    assert tl_scope.origin_1000 == (0.0, 0.0)

    # Bottom-right corner: padding expands beyond the frame → clamped to W/H.
    br = Mark(
        mark_id="ax_1", screen_id=_SCREEN, bbox=(900, 900, 1000, 1000), center=(950, 950),
        source="accessibility",
    )
    br_scope = _build_scope_crop(_shot(), scope_mark=br, width_px=frame_w, height_px=frame_h)
    assert br_scope is not None
    assert (br_scope.crop.width, br_scope.crop.height) == (210, 210)
    assert br_scope.origin_1000 == (895.0, 895.0)


def test_scope_crop_returns_none_for_undecodable_payload() -> None:
    scope_mark = Mark(
        mark_id="ax_1", screen_id=_SCREEN, bbox=(0, 0, 100, 100), center=(50, 50),
        source="accessibility",
    )
    result = _build_scope_crop(
        type("Shot", (), {"base64_data": "not-a-real-image"})(),
        scope_mark=scope_mark,
        width_px=1000,
        height_px=2000,
    )
    assert result is None


# ----------------------------------------------------------------------
# locate_target with a scope: provider input, back-mapping, F binding
# ----------------------------------------------------------------------


def test_scoped_locate_queries_crop_and_maps_mark_back(base_state) -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(bbox=[100, 100, 200, 200])
    device = _PNGDevice(png, frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id="ax_1",
    )
    outcome = locate_target(state, _config(provider, device))

    assert outcome.success is True
    # The provider inspected the 440x440 crop, not the full frame.
    assert provider.received_size == (440, 440)
    assert provider.received_payload != png
    assert outcome.scope_mark_id == "ax_1"
    assert outcome.scope_crop_size_px == (440, 440)
    assert outcome.scope_frame_size_px == (frame_w, frame_h)
    assert outcome.scope_bbox_1000 == pytest.approx((190.0, 190.0, 410.0, 410.0))
    # Crop-local box mapped back to full-screen 0-1000.
    assert outcome.mark is not None
    assert outcome.mark.bbox == pytest.approx((212.0, 212.0, 234.0, 234.0))
    assert outcome.mark.center == pytest.approx((223.0, 223.0))
    # The mark binds F (full frame), not the crop.
    assert outcome.raw_screenshot_hash == compute_raw_screenshot_hash(png)
    # The provider hash covers the crop it actually received.
    assert outcome.provider_input_hash == hashlib.sha256(
        provider.received_payload.encode("utf-8")
    ).hexdigest()[:16]
    # The binding handed to the provider stays full-frame.
    assert provider.requests[0]["screen_binding"]["raw_screenshot_hash"] == (
        compute_raw_screenshot_hash(png)
    )
    assert (
        provider.requests[0]["screen_binding"]["width"],
        provider.requests[0]["screen_binding"]["height"],
    ) == (frame_w, frame_h)


def test_scoped_locate_unscoped_behavior_unchanged(base_state) -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(bbox=[400, 400, 600, 600])
    device = _PNGDevice(png, frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id=None,
    )
    outcome = locate_target(state, _config(provider, device))

    assert outcome.success is True
    assert outcome.scope_mark_id is None
    assert outcome.scope_bbox_1000 is None
    # Without a scope the provider sees the full frame and the box passes through.
    assert provider.received_size == (frame_w, frame_h)
    assert outcome.mark is not None
    assert outcome.mark.bbox == (400.0, 400.0, 600.0, 600.0)


def test_scoped_locate_no_candidate_fails_closed(base_state) -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(failure_code="grounding_no_candidate")
    device = _PNGDevice(png, frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id="ax_1",
    )
    outcome = locate_target(state, _config(provider, device))
    assert outcome.success is False
    assert outcome.failure_code == "grounding_no_candidate"


def test_scoped_locate_multiple_boxes_fails_closed(base_state) -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(
        bboxes=[[100, 100, 300, 300], [500, 500, 700, 700]]
    )
    device = _PNGDevice(png, frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id="ax_1",
    )
    outcome = locate_target(state, _config(provider, device))
    assert outcome.success is False
    assert outcome.failure_code == "grounding_ambiguous"


def test_scoped_locate_unknown_scope_fails_closed_without_provider_call(
    base_state,
) -> None:
    frame_w, frame_h = 2000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(bbox=[100, 100, 200, 200])
    device = _PNGDevice(png, frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id="ax_missing",
    )
    outcome = locate_target(state, _config(provider, device))
    assert outcome.success is False
    assert outcome.failure_code == "scope_mark_unknown"
    assert provider.requests == []


def test_scoped_locate_undecodable_screenshot_fails_closed(base_state) -> None:
    frame_w, frame_h = 2000, 2000
    provider = _RecordingProvider(bbox=[100, 100, 200, 200])
    device = _PNGDevice("not-a-real-image", frame_w, frame_h)
    state = _locate_state(
        base_state, png_b64="not-a-real-image", width=frame_w, height=frame_h,
        scope_bbox=(200, 200, 400, 400), scope_mark_id="ax_1",
    )
    outcome = locate_target(state, _config(provider, device))
    assert outcome.success is False
    assert outcome.failure_code == "scope_crop_failed"
    assert provider.requests == []


# ----------------------------------------------------------------------
# Integration: locate(scope) → registered mark → tap lands on mapped coords
# ----------------------------------------------------------------------


def test_locate_scope_then_grounded_tap_lands_on_mapped_coordinates(
    base_state,
) -> None:
    """Full chain: scoped locate registers a full-screen mapped mark, and the
    next tap executes at the mapped center converted to device pixels."""
    frame_w, frame_h = 1000, 2000
    png = _png_b64(frame_w, frame_h)
    provider = _RecordingProvider(bbox=[250, 250, 350, 350])
    device = _PNGDevice(png, frame_w, frame_h)

    state = _locate_state(
        base_state, png_b64=png, width=frame_w, height=frame_h,
        scope_bbox=(400, 400, 600, 600), scope_mark_id="ax_1",
    )
    locate_result = execute_node(state, _config(provider, device))
    assert locate_result["action_result"]["success"] is True
    assert locate_result["locate_count"] == 1
    registry = MarkRegistry.from_dict(locate_result["mark_registry"])
    assert registry is not None
    locate_mark = registry.marks["locate_1"]
    # scope bbox (400,400,600,600) → px (400,800,600,1200); pad 10/20 →
    # crop (390,780,610,1220); LA box [250,250,350,350] → full (445,445,467,467).
    assert locate_mark.bbox == pytest.approx((445.0, 445.0, 467.0, 467.0))
    assert locate_mark.center == pytest.approx((456.0, 456.0))

    state_after_locate = {
        **base_state,
        "screen_width": frame_w,
        "screen_height": frame_h,
        "mark_registry": locate_result["mark_registry"],
        "locate_count": locate_result["locate_count"],
    }
    grounded = ground_intent_to_action(
        {"_metadata": "intent", "action": "Tap", "target_mark_id": "locate_1"},
        mark_registry=MarkRegistry.from_dict(state_after_locate["mark_registry"]),
        screen_id=_SCREEN,
    )
    assert grounded["action"] == "Tap"
    assert grounded["element"] == [456, 456]

    state_after_locate["action_parsed"] = grounded
    state_after_locate["action_raw"] = '{"type":"intent","action":"tap","target_mark_id":"locate_1"}'
    tap_result = execute_node(
        state_after_locate,
        {"configurable": {"device_factory": device, "verbose": False}},
    )
    assert tap_result["action_result"]["success"] is True
    # 456 rel → x=456px; 456 rel over 2000px → y=912px.
    assert convert_relative_to_absolute([456, 456], frame_w, frame_h) == (456, 912)
    assert device.calls[-1] == ("tap", (456, 912, "device-1"), {})
