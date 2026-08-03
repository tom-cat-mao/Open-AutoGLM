"""F1 locate tool: visual-provider mark registration for the current screen.

The model emits ``{"type":"intent","action":"locate","target_text_hint":"..."}``
when the target it needs is not present among the current screen's executable
marks. The harness answers with a single LocateAnything query against the
current screenshot; the grid-level box is merged into the mark registry as a
``locate_N`` mark (same screen binding, new ``mark_set_version``) so the next
plan round can act on a real ``target_mark_id``.

Fail-closed contract (P0 #9 / P0 #8):
- single hint, single query, ``structure_mode=off`` semantics: one valid box is
  required; zero boxes → ``no_candidate``, multiple boxes → ``ambiguous``.
- the screenshot is re-captured and its raw hash must equal the registry hash;
  a changed screen (hash mismatch) is rejected so marks are never bound to a
  stale snapshot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from phone_agent.config.policy import (
    LOCATE_MAX_MARKS_PER_SCREEN,
    LOCATE_MAX_PER_RUN,
)
from phone_agent.graph.marks import (
    Mark,
    MarkRegistry,
    compute_raw_screenshot_hash,
)
from phone_agent.graph.trace import emit_trace
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


def _current_binding(state: dict[str, Any]) -> ScreenBinding | None:
    """Rebuild the screen binding from the CURRENT mark registry.

    The registry owns the authoritative screen identity; the re-captured
    screenshot must hash to the same raw bytes (P0 #9) or locate fails closed.
    """

    registry = MarkRegistry.from_dict(state.get("mark_registry"))
    if registry is None or not registry.screen_id:
        return None
    return ScreenBinding(
        screen_id=registry.screen_id,
        raw_screenshot_hash=registry.raw_screenshot_hash or "",
        width=int(state.get("screen_width") or 0),
        height=int(state.get("screen_height") or 0),
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
) -> Mark | None:
    """Convert one provider candidate into a trace-safe registry Mark."""

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

    registry = MarkRegistry.from_dict(state.get("mark_registry"))
    binding = _current_binding(state)
    if binding is None:
        return LocateOutcome(
            success=False,
            failure_code="registry_missing",
            message="no mark registry binding for the current screen",
        )
    captured_hash = compute_raw_screenshot_hash(screenshot_b64)
    if captured_hash != binding.raw_screenshot_hash:
        # P0 #9: the snapshot changed since the last observation. Registering a
        # mark against a stale screen would break the hash binding, so locate
        # fails closed and asks for a re-observation instead.
        return LocateOutcome(
            success=False,
            failure_code="screen_changed",
            message="screen changed since last observation; re-observe before locate",
            screen_id=binding.screen_id,
            raw_screenshot_hash=captured_hash,
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
        )

    provider = build_locate_provider(configurable)
    if provider is None:
        return LocateOutcome(
            success=False,
            failure_code="provider_unavailable",
            message="no visual locate provider available",
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
        )

    try:
        result = provider.provide_marks(
            screenshot,
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
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
        )

    latency_ms = _elapsed_ms(started)
    provider_name = getattr(provider, "name", result.provider)
    marks = list(result.marks or [])
    candidates = list(result.candidates or [])
    candidate_count = len(candidates) or len(marks)
    if not result.success:
        return LocateOutcome(
            success=False,
            failure_code=result.failure_code or "grounding_failed",
            message=result.message or (result.failure_code or "grounding_failed"),
            provider=provider_name,
            provider_input_hash=result.provider_input_hash,
            latency_ms=latency_ms,
            candidate_count=candidate_count,
            screen_id=result.screen_id or binding.screen_id,
            raw_screenshot_hash=result.raw_screenshot_hash or binding.raw_screenshot_hash,
        )
    if len(marks) != 1:
        # structure_mode=off semantics: exactly one executable box is required.
        code = "grounding_ambiguous" if len(marks) > 1 else "grounding_no_candidate"
        return LocateOutcome(
            success=False,
            failure_code=code,
            message=(
                "locate expected exactly one candidate box"
                if code == "grounding_ambiguous"
                else "locate found no candidate box"
            ),
            provider=provider_name,
            provider_input_hash=result.provider_input_hash,
            latency_ms=latency_ms,
            candidate_count=candidate_count,
            screen_id=result.screen_id or binding.screen_id,
            raw_screenshot_hash=result.raw_screenshot_hash or binding.raw_screenshot_hash,
        )

    next_index = locate_count + 1
    mark = _coerce_candidate_mark(
        marks[0],
        screen_id=binding.screen_id,
        mark_id=f"locate_{next_index}",
        provider=provider_name,
        text_summary=hint,
    )
    if mark is None:
        return LocateOutcome(
            success=False,
            failure_code="provider_error",
            message="locate candidate could not be coerced into a mark",
            provider=provider_name,
            latency_ms=latency_ms,
            screen_id=binding.screen_id,
            raw_screenshot_hash=binding.raw_screenshot_hash,
        )
    return LocateOutcome(
        success=True,
        mark=mark,
        provider=provider_name,
        provider_input_hash=result.provider_input_hash,
        latency_ms=latency_ms,
        candidate_count=candidate_count,
        screen_id=binding.screen_id,
        raw_screenshot_hash=binding.raw_screenshot_hash,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def trace_safe_payload(outcome: LocateOutcome, *, hint_length: int) -> dict[str, Any]:
    """Build a trace-safe payload for a locate outcome (no hint text)."""

    return {
        "success": outcome.success,
        "failure_code": outcome.failure_code,
        "provider": outcome.provider,
        "provider_input_hash": outcome.provider_input_hash,
        "latency_ms": outcome.latency_ms,
        "candidate_count": outcome.candidate_count,
        "screen_id": outcome.screen_id,
        "raw_screenshot_hash": outcome.raw_screenshot_hash,
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
