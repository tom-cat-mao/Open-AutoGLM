"""F1 locate tool: visual-provider mark registration for the current screen.

The model emits ``{"type":"intent","action":"locate","target_text_hint":"...",
"scope_mark_id":"..."}`` (form A: single container) or
``scope_start_mark_id``/``scope_end_mark_id`` (form B: interval) when the
target it needs is not present among the current screen's executable marks.
The harness answers with a single LocateAnything query against the current
screenshot; the grid-level box is merged into the mark registry as a
``locate_N`` mark (same screen binding, new ``mark_set_version``) so the next
plan round can act on a real ``target_mark_id``.

Scoped locate (S2/P1): scope is MANDATORY — the tool contract is "point at the
region first, then at the target". Form A crops F to a single container mark's
bbox; form B crops to ``[start.top, end.top) x (container width | full width)``
(no end → to the bottom of start's own bbox). The current screenshot F is
cropped (0-1000 -> device pixels, expanded by ``LOCATE_SCOPE_PADDING_RATIO`` on
each side, clamped to the frame) at ORIGINAL resolution, and the provider query
runs against the crop instead of the downscaled full frame — small/dense
targets get both higher resolution and a reduced ambiguity set. The provider's
crop-local 0-1000 box is affinely mapped back to full-screen 0-1000 coordinates
before mark registration, and the mark still binds F's hash (P0 #9: the binding
describes the full frame the model will act on, not the internal crop).

Fail-closed contract (P0 #9 / P0 #8):
- single hint, single query, ``structure_mode=off`` semantics: one valid box is
  required; zero boxes → ``no_candidate``, multiple boxes → ``ambiguous``.
- atomic observe+query: the screenshot F is captured at execute time and the
  provider query runs against F. The binding is constructed FROM F
  (``hash_F``), so any merged mark is bound to the frame the provider actually
  saw — P0 #9 holds by construction, not by two-frame hash comparison.
  Screen drift between the plan observation and F never rejects locate; it is
  recorded as ``observation_drifted`` for diagnostics.
- scoped failures (missing/conflicting scope, scope mark unknown/invalidated,
  cross-screen interval anchors, undecodable crop, degenerate crop region) fail
  closed: never a silent fallback to a full-frame query.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Sequence

from langchain_core.runnables import RunnableConfig
from PIL import Image

from phone_agent.config.policy import (
    LOCATE_MAX_MARKS_PER_SCREEN,
    LOCATE_MAX_PER_RUN,
    LOCATE_SCOPE_PADDING_RATIO,
)
from phone_agent.graph.marks import (
    Mark,
    MarkRegistry,
    compute_raw_screenshot_hash,
)
from phone_agent.graph.trace import emit_trace, save_debug_screenshot
from phone_agent.grounding.factory import build_locate_provider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    ScreenBinding,
)


@dataclass(frozen=True)
class LocateOutcome:
    """Result of one locate query against the current screen."""

    success: bool
    failure_code: str | None = None
    message: str | None = None
    mark: Mark | None = None
    provider: str | None = None
    provider_input_hash: str | None = None
    latency_ms: int | None = None
    candidate_count: int = 0
    screen_id: str | None = None
    raw_screenshot_hash: str | None = None
    observation_drifted: bool = False
    # S2/P1: scoped-locate diagnostics (trace-safe; mark ids and coordinates only).
    scope_mark_id: str | None = None
    scope_start_mark_id: str | None = None
    scope_end_mark_id: str | None = None
    # Padded crop region expressed in full-frame 0-1000 space.
    scope_bbox_1000: tuple[float, float, float, float] | None = None
    # Crop size in device pixels (the provider input resolution).
    scope_crop_size_px: tuple[int, int] | None = None
    # Full-frame size in device pixels the crop was derived from.
    scope_frame_size_px: tuple[int, int] | None = None


@dataclass(frozen=True)
class ScopedImage:
    """A cropped region of F, re-encoded, ready for a provider request.

    ``width``/``height`` are the crop's own pixel dimensions; the provider's
    ``_prepare_image`` thumbnail runs on the crop at original resolution, so a
    small scope region keeps far more detail than the downscaled full frame.
    """

    base64_data: str
    width: int
    height: int
    mime_type: str = "image/png"


@dataclass(frozen=True)
class ScopeCrop:
    """Geometry of a scoped locate crop, for affine back-mapping (S2).

    LA emits boxes normalized to the image it processed (the crop). Because
    both the crop-local space and the full-frame 0-1000 space are linear
    scalings of the same device-pixel geometry (and PIL ``thumbnail`` is
    aspect-preserving), the back-map is an exact affine transform:

    ``full = origin_1000 + box * size_1000 / 1000`` per edge.
    """

    origin_1000: tuple[float, float]
    size_1000: tuple[float, float]
    crop: ScopedImage

    def map_box_to_full(self, box: Sequence[float]) -> tuple[float, float, float, float]:
        ox, oy = self.origin_1000
        sx, sy = self.size_1000
        bx1, by1, bx2, by2 = (float(value) for value in box)
        return (
            ox + bx1 * sx / 1000.0,
            oy + by1 * sy / 1000.0,
            ox + bx2 * sx / 1000.0,
            oy + by2 * sy / 1000.0,
        )


# P1 form B interval geometry: container-like accessibility roles whose bbox
# supplies the interval's horizontal extent (and, without an end mark, its
# bottom edge). Text anchors (month titles, labels) are NOT containers: their
# intervals span the full screen width instead.
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


def _is_container_like(mark: Mark) -> bool:
    """Whether a mark's role explicitly denotes a container region."""

    return str(mark.role or "").casefold() in _CONTAINER_ROLE_NAMES


def _interval_region_1000(start: Mark, end: Mark | None) -> tuple[float, float, float, float]:
    """P1 form B: ``[start.top, end.top) x (container width | full width)``.

    Without an end mark the interval runs to the bottom of start's own bbox
    (the only container-bottom evidence a registry mark carries). The start
    mark's bbox supplies the horizontal extent only when it is container-like;
    text anchors (e.g. month titles) span the full screen width instead, which
    is what makes the calendar-grid case work: start/end titles clip the
    vertical band while the crop keeps full width. Padding/clamp/mapping are
    inherited from the shared ScopeCrop path.
    """

    if _is_container_like(start):
        x1, x2 = float(start.bbox[0]), float(start.bbox[2])
    else:
        x1, x2 = 0.0, 1000.0
    y1 = float(start.bbox[1])
    y2 = float(end.bbox[1]) if end is not None else float(start.bbox[3])
    return (x1, y1, x2, y2)


def _build_scope_crop(
    screenshot: Any,
    *,
    scope_mark: Mark | None = None,
    region_bbox_1000: Sequence[float] | None = None,
    width_px: int,
    height_px: int,
    padding_ratio: float = LOCATE_SCOPE_PADDING_RATIO,
) -> ScopeCrop | None:
    """Crop F to the scope region's padded, clamped bbox at original resolution.

    ``region_bbox_1000`` (P1 form B interval) takes precedence over
    ``scope_mark.bbox`` (P1 form A single container) when both are provided.

    Coordinate chain: region bbox (0-1000) → device pixels via the canonical
    frame size (state ``screen_width``/``screen_height``, the same dims the tap
    chain uses for ``convert_relative_to_absolute``) → pad each side by
    ``padding_ratio * box extent`` → clamp into ``[0, image]`` → crop. Returns
    ``None`` (fail-closed) when F cannot be decoded as an image or the padded
    region is degenerate; the caller must never fall back to a full-frame query.
    """

    raw = getattr(screenshot, "base64_data", None)
    if not raw or width_px <= 0 or height_px <= 0:
        return None
    if region_bbox_1000 is not None:
        sx1, sy1, sx2, sy2 = (float(value) for value in region_bbox_1000)
    elif scope_mark is not None:
        sx1, sy1, sx2, sy2 = (float(value) for value in scope_mark.bbox)
    else:
        return None
    # An inverted/degenerate region (e.g. an interval whose end mark sits above
    # its start mark) is fail-closed, never a 1px sliver.
    if sx2 < sx1 or sy2 < sy1:
        return None
    # P1: a region that already covers the full frame is a pass-through — no
    # decode/crop/re-encode needed (identity mapping at original resolution).
    # This also keeps locate correct when the screenshot payload is not
    # decodable by PIL (test doubles / dry runs): the provider still receives
    # the untouched full-frame payload, exactly like an unscoped query.
    if sx1 <= 0.0 and sy1 <= 0.0 and sx2 >= 1000.0 and sy2 >= 1000.0:
        return ScopeCrop(
            origin_1000=(0.0, 0.0),
            size_1000=(1000.0, 1000.0),
            crop=ScopedImage(
                base64_data=raw,
                width=int(width_px),
                height=int(height_px),
            ),
        )
    try:
        image = Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")
    except Exception:
        return None
    image_w, image_h = image.size
    if image_w <= 0 or image_h <= 0:
        return None
    px1, py1 = sx1 * width_px / 1000.0, sy1 * height_px / 1000.0
    px2, py2 = sx2 * width_px / 1000.0, sy2 * height_px / 1000.0
    pad_x = max(0.0, (px2 - px1) * padding_ratio)
    pad_y = max(0.0, (py2 - py1) * padding_ratio)
    cx1 = int(max(0.0, min(px1 - pad_x, image_w - 1)))
    cy1 = int(max(0.0, min(py1 - pad_y, image_h - 1)))
    cx2 = int(min(image_w, max(px2 + pad_x, cx1 + 1)))
    cy2 = int(min(image_h, max(py2 + pad_y, cy1 + 1)))
    if cx2 <= cx1 or cy2 <= cy1:
        return None
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


def _scope_failure(
    failure_code: str,
    message: str,
    *,
    scope_mark_id: str | None = None,
    scope_start_mark_id: str | None = None,
    scope_end_mark_id: str | None = None,
    screen_id: str | None,
    raw_screenshot_hash: str | None,
    observation_drifted: bool,
) -> LocateOutcome:
    return LocateOutcome(
        success=False,
        failure_code=failure_code,
        message=message,
        scope_mark_id=scope_mark_id,
        scope_start_mark_id=scope_start_mark_id,
        scope_end_mark_id=scope_end_mark_id,
        screen_id=screen_id,
        raw_screenshot_hash=raw_screenshot_hash,
        observation_drifted=observation_drifted,
    )


def _binding_for_screenshot(
    state: dict[str, Any],
    registry: MarkRegistry,
    screenshot: Any,
    captured_hash: str,
) -> ScreenBinding:
    """Build the atomic locate binding FROM the freshly captured frame F.

    The binding carries F's own raw hash (``hash_F``): the provider is queried
    against F and the merged mark is therefore bound to the frame the provider
    actually saw. The registry's screen identity (``screen_id`` / semantic id /
    epoch) is preserved; drift between the plan observation and F is recorded
    by the caller (``observation_drifted``), never rejected.
    """

    return ScreenBinding(
        screen_id=registry.screen_id,
        raw_screenshot_hash=captured_hash,
        width=int(getattr(screenshot, "width", 0) or state.get("screen_width") or 0),
        height=int(getattr(screenshot, "height", 0) or state.get("screen_height") or 0),
        current_app=state.get("current_app"),
        semantic_screen_id=registry.semantic_screen_id,
        observation_epoch=registry.observation_epoch,
        mark_set_version=registry.mark_set_version,
        perceptual_hash=registry.perceptual_hash,
    )


def _locate_mark_count(registry: MarkRegistry | None) -> int:
    """Count locate marks already merged onto the current screen."""

    if registry is None:
        return 0
    return sum(
        1 for mark_id in registry.marks if str(mark_id).startswith("locate_")
    )


def _coerce_candidate_mark(
    candidate: MarkCandidate | dict[str, Any] | None,
    *,
    screen_id: str,
    mark_id: str,
    provider: str,
    text_summary: str | None,
    bbox_override: Sequence[float] | None = None,
    center_override: Sequence[float] | None = None,
) -> Mark | None:
    """Convert one provider candidate into a trace-safe registry Mark.

    ``bbox_override``/``center_override`` carry the S2 affine back-mapping for
    scoped locate: the provider's crop-local box is mapped to full-frame
    0-1000 before the Mark is constructed, so the coercion path (and its
    fail-closed validation) stays single.
    """

    if candidate is None:
        return None
    if isinstance(candidate, dict):
        bbox = candidate.get("bbox") or candidate.get("bounds")
        center = candidate.get("center")
        confidence = candidate.get("confidence")
    else:
        bbox = candidate.bbox
        center = candidate.center
        confidence = candidate.confidence
    if bbox_override is not None:
        bbox = list(bbox_override)
    if center_override is not None:
        center = list(center_override)
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        center = (
            (float(bbox[0]) + float(bbox[2])) / 2,
            (float(bbox[1]) + float(bbox[3])) / 2,
        )
    try:
        return Mark(
            mark_id=mark_id,
            screen_id=screen_id,
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            center=(float(center[0]), float(center[1])),
            source=provider,
            confidence=1.0,
            role=None,
            text_summary=text_summary,
            password=False,
        )
    except (TypeError, ValueError):
        return None


def locate_target(
    state: dict[str, Any], config: RunnableConfig
) -> LocateOutcome:
    """Run one locate query for ``state.action_parsed.target_text_hint``.

    Budget and per-screen limits are enforced here (before any provider call),
    so a runaway model cannot burn the visual provider budget: exhaustion is a
    fail-closed rejection with a stable ``failure_code``.
    """

    started = time.perf_counter()
    action = state.get("action_parsed") or {}
    hint = str(action.get("target_text_hint") or "").strip()
    if not hint:
        return LocateOutcome(
            success=False,
            failure_code="missing_field",
            message="Locate requires a non-empty target_text_hint",
        )
    # P1: scope is mandatory — either form A (scope_mark_id, single container)
    # or form B (scope_start_mark_id + optional scope_end_mark_id interval).
    # The validator/grounding already enforce this; locate_target re-checks
    # fail-closed so a direct caller can never run an unscoped query.
    scope_mark_id = str(action.get("scope_mark_id") or "").strip() or None
    scope_start_mark_id = str(action.get("scope_start_mark_id") or "").strip() or None
    scope_end_mark_id = str(action.get("scope_end_mark_id") or "").strip() or None
    if scope_mark_id is not None and scope_start_mark_id is not None:
        return LocateOutcome(
            success=False,
            failure_code="scope_conflict",
            message="Locate accepts either scope_mark_id or scope_start_mark_id, not both",
            scope_mark_id=scope_mark_id,
            scope_start_mark_id=scope_start_mark_id,
        )
    if scope_mark_id is None and scope_start_mark_id is None:
        return LocateOutcome(
            success=False,
            failure_code="missing_field",
            message="Locate requires scope_mark_id or scope_start_mark_id",
        )
    if scope_start_mark_id is None and scope_end_mark_id is not None:
        return LocateOutcome(
            success=False,
            failure_code="missing_field",
            message="Locate scope_end_mark_id requires scope_start_mark_id",
            scope_start_mark_id=scope_start_mark_id,
            scope_end_mark_id=scope_end_mark_id,
        )

    locate_count = int(state.get("locate_count") or 0)
    if locate_count >= LOCATE_MAX_PER_RUN:
        return LocateOutcome(
            success=False,
            failure_code="locate_budget_exhausted",
            message=f"locate budget exhausted ({LOCATE_MAX_PER_RUN} per run)",
        )

    configurable = config.get("configurable", {}) if config else {}
    device_factory = configurable.get("device_factory")
    if device_factory is None or not hasattr(device_factory, "get_screenshot"):
        return LocateOutcome(
            success=False,
            failure_code="provider_unavailable",
            message="no device factory for screenshot capture",
        )

    try:
        screenshot = device_factory.get_screenshot(state.get("device_id"))
    except Exception as exc:
        return LocateOutcome(
            success=False,
            failure_code="screenshot_failed",
            message=f"screenshot capture failed: {type(exc).__name__}",
        )
    screenshot_b64 = getattr(screenshot, "base64_data", None)
    if not screenshot_b64:
        return LocateOutcome(
            success=False,
            failure_code="screenshot_failed",
            message="screenshot has no base64 payload",
        )
    save_debug_screenshot(config, state, "locate_frame", screenshot_b64)

    registry = MarkRegistry.from_dict(state.get("mark_registry"))
    if registry is None:
        return LocateOutcome(
            success=False,
            failure_code="registry_missing",
            message="no mark registry binding for the current screen",
            scope_mark_id=scope_mark_id,
        )
    # S1/P1: referenced scope marks must exist in the CURRENT registry (P0 #8:
    # only existing marks may be referenced) and must not be invalidated
    # locate_* marks (S4). Both fail closed before any screenshot/provider work.
    # Form A: scope_mark_id. Form B: scope_start_mark_id (+ optional end).
    scope_mark: Mark | None = None
    scope_start_mark: Mark | None = None
    scope_end_mark: Mark | None = None
    invalidated = {
        str(mark_id) for mark_id in (state.get("invalidated_mark_ids") or [])
    }
    if scope_mark_id is not None:
        scope_mark = registry.get(scope_mark_id)
        if scope_mark is None:
            return _scope_failure(
                "scope_mark_unknown",
                f"scope mark not in registry: {scope_mark_id}",
                scope_mark_id=scope_mark_id,
                screen_id=registry.screen_id,
                raw_screenshot_hash=None,
                observation_drifted=False,
            )
        if scope_mark_id in invalidated:
            return _scope_failure(
                "scope_mark_invalidated",
                f"scope mark has been invalidated: {scope_mark_id}",
                scope_mark_id=scope_mark_id,
                screen_id=registry.screen_id,
                raw_screenshot_hash=None,
                observation_drifted=False,
            )
    else:
        scope_start_mark = registry.get(scope_start_mark_id)
        if scope_start_mark is None:
            return _scope_failure(
                "scope_mark_unknown",
                f"scope start mark not in registry: {scope_start_mark_id}",
                scope_start_mark_id=scope_start_mark_id,
                screen_id=registry.screen_id,
                raw_screenshot_hash=None,
                observation_drifted=False,
            )
        if scope_start_mark_id in invalidated:
            return _scope_failure(
                "scope_mark_invalidated",
                f"scope start mark has been invalidated: {scope_start_mark_id}",
                scope_start_mark_id=scope_start_mark_id,
                screen_id=registry.screen_id,
                raw_screenshot_hash=None,
                observation_drifted=False,
            )
        if scope_end_mark_id is not None:
            scope_end_mark = registry.get(scope_end_mark_id)
            if scope_end_mark is None:
                return _scope_failure(
                    "scope_mark_unknown",
                    f"scope end mark not in registry: {scope_end_mark_id}",
                    scope_start_mark_id=scope_start_mark_id,
                    scope_end_mark_id=scope_end_mark_id,
                    screen_id=registry.screen_id,
                    raw_screenshot_hash=None,
                    observation_drifted=False,
                )
            if scope_end_mark_id in invalidated:
                return _scope_failure(
                    "scope_mark_invalidated",
                    f"scope end mark has been invalidated: {scope_end_mark_id}",
                    scope_start_mark_id=scope_start_mark_id,
                    scope_end_mark_id=scope_end_mark_id,
                    screen_id=registry.screen_id,
                    raw_screenshot_hash=None,
                    observation_drifted=False,
                )
            if (
                scope_start_mark.screen_id != registry.screen_id
                or scope_end_mark.screen_id != registry.screen_id
            ):
                # Registry marks share the registry screen by construction; this
                # is defense-in-depth for a manually assembled registry.
                return _scope_failure(
                    "scope_mark_unknown",
                    "scope start/end marks must belong to the current screen",
                    scope_start_mark_id=scope_start_mark_id,
                    scope_end_mark_id=scope_end_mark_id,
                    screen_id=registry.screen_id,
                    raw_screenshot_hash=None,
                    observation_drifted=False,
                )
    captured_hash = compute_raw_screenshot_hash(screenshot_b64)
    # Atomic observe+query (H1): the binding is constructed from F itself, so
    # screen drift between the plan observation and F is recorded for
    # diagnostics (observation_drifted), never a rejection. P0 #9 is satisfied
    # by construction: the mark binds the frame the provider actually saw.
    binding = _binding_for_screenshot(state, registry, screenshot, captured_hash)
    observation_drifted = bool(registry.raw_screenshot_hash) and (
        captured_hash != registry.raw_screenshot_hash
    )

    if _locate_mark_count(registry) >= LOCATE_MAX_MARKS_PER_SCREEN:
        return LocateOutcome(
            success=False,
            failure_code="locate_screen_mark_limit",
            message=(
                f"locate mark limit reached for this screen "
                f"({LOCATE_MAX_MARKS_PER_SCREEN} per screen)"
            ),
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )

    provider = build_locate_provider(configurable)
    if provider is None:
        return LocateOutcome(
            success=False,
            failure_code="provider_unavailable",
            message="no visual locate provider available",
            scope_mark_id=scope_mark_id,
            scope_start_mark_id=scope_start_mark_id,
            scope_end_mark_id=scope_end_mark_id,
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )

    # S2/P1: with a scope, the provider query runs against the high-resolution
    # crop of F instead of the (internally downscaled) full frame. Form A uses
    # the container mark's bbox; form B uses the interval region derived from
    # the start/end anchor marks. The crop is built from the freshly captured F
    # and the binding stays full-frame (mark still binds hash_F); a failed crop
    # is fail-closed, never a silent full-frame fallback.
    provider_screenshot: Any = screenshot
    scope: ScopeCrop | None = None
    scope_region_1000: tuple[float, float, float, float] | None = None
    if scope_mark is not None:
        scope_region_1000 = tuple(float(value) for value in scope_mark.bbox)
    elif scope_start_mark is not None:
        scope_region_1000 = _interval_region_1000(scope_start_mark, scope_end_mark)
    if scope_region_1000 is not None:
        width_px = int(
            state.get("screen_width")
            or getattr(screenshot, "width", 0)
            or 0
        )
        height_px = int(
            state.get("screen_height")
            or getattr(screenshot, "height", 0)
            or 0
        )
        scope = _build_scope_crop(
            screenshot,
            region_bbox_1000=scope_region_1000,
            width_px=width_px,
            height_px=height_px,
        )
        if scope is None:
            return _scope_failure(
                "scope_crop_failed",
                _scoped_failure_message(str(state.get("lang") or "cn"), "scope_crop_failed"),
                scope_mark_id=scope_mark_id,
                scope_start_mark_id=scope_start_mark_id,
                scope_end_mark_id=scope_end_mark_id,
                screen_id=binding.screen_id,
                raw_screenshot_hash=binding.raw_screenshot_hash,
                observation_drifted=observation_drifted,
            )
        provider_screenshot = scope.crop

    try:
        result = provider.provide_marks(
            provider_screenshot,
            binding,
            hints=[MarkProviderHint(text=hint, source="locate")],
            timeout=float(configurable.get("grounding_timeout", 10.0) or 10.0),
        )
    except Exception as exc:
        return LocateOutcome(
            success=False,
            failure_code="provider_error",
            message=f"locate provider error: {type(exc).__name__}",
            provider=getattr(provider, "name", None),
            latency_ms=_elapsed_ms(started),
            scope_mark_id=scope_mark_id,
            scope_start_mark_id=scope_start_mark_id,
            scope_end_mark_id=scope_end_mark_id,
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )

    latency_ms = _elapsed_ms(started)
    provider_name = getattr(provider, "name", result.provider)
    marks = list(result.marks or [])
    candidates = list(result.candidates or [])
    candidate_count = len(candidates) or len(marks)
    lang = str(state.get("lang") or "cn")
    if not result.success:
        failure_code = result.failure_code or "grounding_failed"
        return LocateOutcome(
            success=False,
            failure_code=failure_code,
            message=_scoped_failure_message(lang, failure_code)
            if failure_code in {"grounding_no_candidate", "grounding_ambiguous"}
            else (result.message or failure_code),
            provider=provider_name,
            provider_input_hash=result.provider_input_hash,
            latency_ms=latency_ms,
            candidate_count=candidate_count,
            scope_mark_id=scope_mark_id,
            scope_start_mark_id=scope_start_mark_id,
            scope_end_mark_id=scope_end_mark_id,
            screen_id=result.screen_id or binding.screen_id,
            raw_screenshot_hash=result.raw_screenshot_hash or binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )
    if len(marks) != 1:
        # structure_mode=off semantics: exactly one executable box is required.
        # P1: the 0-box/multi-box failure message appends an adjust/expand hint
        # in the run's language (information, never an instruction).
        code = "grounding_ambiguous" if len(marks) > 1 else "grounding_no_candidate"
        message = _scoped_failure_message(lang, code)
        return LocateOutcome(
            success=False,
            failure_code=code,
            message=message,
            provider=provider_name,
            provider_input_hash=result.provider_input_hash,
            latency_ms=latency_ms,
            candidate_count=candidate_count,
            scope_mark_id=scope_mark_id,
            screen_id=result.screen_id or binding.screen_id,
            raw_screenshot_hash=result.raw_screenshot_hash or binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )

    # S2 back-mapping: the provider's single box is normalized to the image it
    # processed. With a scope that image is the crop R; the box is affinely
    # mapped back to full-frame 0-1000 (``full = origin_1000 + box * size_1000
    # / 1000`` per edge) before mark registration. Without a scope the box is
    # already full-frame and passes through unchanged.
    next_index = locate_count + 1
    bbox_override: tuple[float, float, float, float] | None = None
    center_override: tuple[float, float] | None = None
    if scope is not None:
        bbox_override = scope.map_box_to_full(marks[0].bbox)
        center_override = (
            (bbox_override[0] + bbox_override[2]) / 2.0,
            (bbox_override[1] + bbox_override[3]) / 2.0,
        )
    mark = _coerce_candidate_mark(
        marks[0],
        screen_id=binding.screen_id,
        mark_id=f"locate_{next_index}",
        provider=provider_name,
        text_summary=hint,
        bbox_override=bbox_override,
        center_override=center_override,
    )
    if mark is None:
        return LocateOutcome(
            success=False,
            failure_code="provider_error",
            message="locate candidate could not be coerced into a mark",
            provider=provider_name,
            latency_ms=latency_ms,
            scope_mark_id=scope_mark_id,
            scope_start_mark_id=scope_start_mark_id,
            scope_end_mark_id=scope_end_mark_id,
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
            observation_drifted=observation_drifted,
        )
    return LocateOutcome(
        success=True,
        mark=mark,
        provider=provider_name,
        provider_input_hash=result.provider_input_hash,
        latency_ms=latency_ms,
        candidate_count=candidate_count,
        scope_mark_id=scope_mark_id,
        scope_start_mark_id=scope_start_mark_id,
        scope_end_mark_id=scope_end_mark_id,
        scope_bbox_1000=(
            scope.map_box_to_full((0.0, 0.0, 1000.0, 1000.0))
            if scope is not None
            else None
        ),
        scope_crop_size_px=(
            (scope.crop.width, scope.crop.height) if scope is not None else None
        ),
        scope_frame_size_px=(
            (int(state.get("screen_width") or 0), int(state.get("screen_height") or 0))
            if scope is not None
            else None
        ),
        screen_id=binding.screen_id,
        raw_screenshot_hash=binding.raw_screenshot_hash,
        observation_drifted=observation_drifted,
    )


def _scoped_failure_message(lang: str, code: str) -> str:
    """Localized locate failure message; 0-box/multi-box/scope-crop failures
    append the adjust/expand-scope hint (P1) and the scope-containment
    semantic hint (information not instruction)."""

    if lang == "cn":
        containment_hint = (
            "；确认 scope 是否【空间包含】目标本身——文字标签不是容器；"
            "若目标在某两个文字锚点之间，可用 start/end 区间锚定。"
        )
    else:
        containment_hint = (
            "; check whether the scope **spatially contains** the target itself "
            "— text labels are not containers; if the target lies between two "
            "text anchors, use start/end interval anchoring."
        )
    if code == "grounding_ambiguous":
        return (
            "找到多个候选框：可调整/扩大 scope 区域后重试"
            if lang == "cn"
            else "locate expected exactly one candidate box; "
            "adjust or expand the scope region and retry"
        ) + containment_hint
    if code == "grounding_no_candidate":
        return (
            "未找到候选框：可调整/扩大 scope 区域后重试"
            if lang == "cn"
            else "locate found no candidate box; "
            "adjust or expand the scope region and retry"
        ) + containment_hint
    if code == "scope_crop_failed":
        return (
            "scope 裁剪失败：截图无法解码或区域退化"
            if lang == "cn"
            else "scope crop failed: screenshot undecodable or degenerate region"
        ) + containment_hint
    return code


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def trace_safe_payload(outcome: LocateOutcome, *, hint_length: int) -> dict[str, Any]:
    """Build a trace-safe payload for a locate outcome (no hint text)."""

    payload = {
        "success": outcome.success,
        "failure_code": outcome.failure_code,
        "provider": outcome.provider,
        "provider_input_hash": outcome.provider_input_hash,
        "latency_ms": outcome.latency_ms,
        "candidate_count": outcome.candidate_count,
        "screen_id": outcome.screen_id,
        "raw_screenshot_hash": outcome.raw_screenshot_hash,
        "observation_drifted": outcome.observation_drifted,
        "hint_length": hint_length,
        "mark_id": outcome.mark.mark_id if outcome.mark is not None else None,
        "bbox": (
            [round(v, 1) for v in outcome.mark.bbox]
            if outcome.mark is not None
            else None
        ),
        "center": (
            [round(v, 1) for v in outcome.mark.center]
            if outcome.mark is not None
            else None
        ),
    }
    if outcome.scope_mark_id is not None or outcome.scope_start_mark_id is not None:
        # Scoped metadata is emitted for both forms (A: scope_mark_id; B:
        # scope_start_mark_id/scope_end_mark_id). Crop metrics are None on
        # pre-crop failures (missing mark, crop failed, budget) — trace-safe
        # None, never a crash.
        if outcome.scope_mark_id is not None:
            payload["scope_mark_id"] = outcome.scope_mark_id
        if outcome.scope_start_mark_id is not None:
            payload["scope_start_mark_id"] = outcome.scope_start_mark_id
            payload["scope_end_mark_id"] = outcome.scope_end_mark_id
        payload["scope_bbox_1000"] = (
            [round(v, 2) for v in outcome.scope_bbox_1000]
            if outcome.scope_bbox_1000 is not None
            else None
        )
        payload["scope_crop_size_px"] = (
            list(outcome.scope_crop_size_px)
            if outcome.scope_crop_size_px is not None
            else None
        )
        payload["scope_frame_size_px"] = (
            list(outcome.scope_frame_size_px)
            if outcome.scope_frame_size_px is not None
            else None
        )
    return payload
