"""Observation builder for screen-bound harness metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phone_agent.graph.marks import (
    MarkRegistry,
    build_mark_topology_digest,
    build_screen_id,
    build_semantic_screen_id,
    compute_perceptual_hash,
    compute_raw_screenshot_hash,
)
from phone_agent.graph.context import sanitize_context_payload
from phone_agent.grounding.provider import MarkProvider, MarkProviderHint, MarkProviderResult, ScreenBinding


def _safe_metadata(value: Any, *, default: str = "") -> str:
    safe = str(sanitize_context_payload(str(value or ""), "message", consumer="inject")).strip()
    return safe[:64] or default


def _safe_int(value: Any, *, default: int = 0, maximum: int = 10_000_000) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(resolved, maximum))


def _safe_coordinate_list(value: Any, *, expected_len: int) -> list[int | float]:
    if not isinstance(value, (list, tuple)) or len(value) != expected_len:
        return []
    coordinates: list[int | float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return []
        if item < 0 or item > 1000:
            return []
        coordinates.append(item)
    return coordinates


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(0.0, min(float(value), 1.0))


def _safe_bool(value: Any) -> bool:
    return value is True


def _safe_length(value: Any) -> int:
    return min(len(str(value or "")), 10_000)


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
    mark_provider_observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "mark_registry": self.mark_registry.to_dict(),
            "mark_provider_observation": self.mark_provider_observation,
        }


def build_mark_provider_hints(
    *, task: str | None = None,
    reflection: str | None = None,
    provider_hints: list[str | dict[str, Any] | MarkProviderHint] | None = None,
    max_hints: int = 3,
) -> list[MarkProviderHint]:
    """Build bounded pre-VLM hints for query-conditioned mark providers."""

    hints: list[MarkProviderHint] = []

    def _provider_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text[:240]

    def _safe_text(value: Any) -> str:
        safe = str(sanitize_context_payload(str(value or ""), "message", consumer="inject")).strip()
        return safe[:240]

    for item in provider_hints or []:
        if isinstance(item, MarkProviderHint):
            text = _provider_text(item.text)
            if text:
                hints.append(
                    MarkProviderHint(
                        text=text,
                        source=_safe_metadata(item.source, default="hint"),
                        role=_safe_text(item.role) or None,
                        intent=_safe_text(item.intent) or None,
                        action=_safe_metadata(item.action) or None,
                    )
                )
        elif isinstance(item, dict):
            text = _provider_text(item.get("text") or item.get("target_text_hint"))
            if text:
                hints.append(
                    MarkProviderHint(
                        text=text,
                        source=_safe_metadata(item.get("source"), default="config"),
                        role=_safe_text(item.get("role")) or None,
                        intent=_safe_text(item.get("intent")) or None,
                        action=_safe_metadata(item.get("action")) or None,
                    )
                )
        else:
            text = _provider_text(item)
            if text:
                hints.append(MarkProviderHint(text=text, source="config"))
    if task and len(hints) < max_hints:
        text = _provider_text(task)
        if text:
            hints.append(MarkProviderHint(text=text, source="task"))
    if reflection and len(hints) < max_hints:
        text = _provider_text(reflection)
        if text:
            hints.append(MarkProviderHint(text=text, source="reflection"))
    return hints[:max_hints]


def _provider_accepts_raw_hints(provider: MarkProvider) -> bool:
    return bool(getattr(provider, "allow_raw_hints", False))


def _redact_provider_hints(hints: list[MarkProviderHint]) -> list[MarkProviderHint]:
    redacted: list[MarkProviderHint] = []
    for hint in hints:
        text = str(sanitize_context_payload(hint.text, "message", consumer="inject")).strip()
        if not text:
            continue
        redacted.append(
            MarkProviderHint(
                text=text[:240],
                source=_safe_metadata(hint.source, default="hint"),
                role=str(sanitize_context_payload(hint.role or "", "message", consumer="inject")).strip()[:240] or None,
                intent=str(sanitize_context_payload(hint.intent or "", "message", consumer="inject")).strip()[:240] or None,
                action=_safe_metadata(hint.action),
            )
        )
    return redacted


def _provider_result_to_marks(result: MarkProviderResult) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    for index, mark in enumerate(result.marks or [], start=1):
        valid = mark.get("valid") if isinstance(mark, dict) else mark.valid
        if not valid:
            continue
        mark_id = mark.get("mark_id") if isinstance(mark, dict) else mark.mark_id
        bbox = mark.get("bbox") if isinstance(mark, dict) else mark.bbox
        center = mark.get("center") if isinstance(mark, dict) else mark.center
        source = mark.get("source") if isinstance(mark, dict) else mark.source
        confidence = mark.get("confidence") if isinstance(mark, dict) else mark.confidence
        role = mark.get("role") if isinstance(mark, dict) else mark.role
        text_summary = mark.get("text_summary") if isinstance(mark, dict) else mark.text_summary
        marks.append(
            {
                "mark_id": mark_id or f"{result.provider}_{index}",
                "bbox": bbox,
                "center": center,
                "source": source or result.provider,
                "confidence": 1.0 if confidence is None else confidence,
                "role": role,
                "text_summary": sanitize_context_payload(text_summary or "", "message", consumer="inject"),
            }
        )
    return marks


def _summarize_provider_result(result: MarkProviderResult) -> dict[str, Any]:
    """Return trace-safe provider metadata without raw hint or mark text."""

    return {
        "provider": _safe_metadata(result.provider, default="unknown"),
        "success": result.success,
        "failure_code": _safe_metadata(result.failure_code),
        "message": _safe_metadata(result.message),
        "screen_id": _safe_metadata(result.screen_id),
        "raw_screenshot_hash": _safe_metadata(result.raw_screenshot_hash),
        "provider_input_hash": _safe_metadata(result.provider_input_hash),
        "latency_ms": _safe_int(result.latency_ms),
        "candidate_count": _safe_int(result.candidate_count),
        "status": _safe_metadata(result.status),
        "hints": [
            {
                key: _safe_metadata(value)
                for key, value in dict(hint).items()
                if key in {"source", "has_text", "text_length", "has_role", "role_length", "has_intent", "intent_length", "action"}
            }
            for hint in list(result.hints or [])[:5]
            if isinstance(hint, dict)
        ],
        "marks": [
            {
                "mark_id": _safe_metadata(mark.get("mark_id") if isinstance(mark, dict) else mark.mark_id),
                "bbox": _safe_coordinate_list(mark.get("bbox") if isinstance(mark, dict) else mark.bbox, expected_len=4),
                "center": _safe_coordinate_list(mark.get("center") if isinstance(mark, dict) else mark.center, expected_len=2),
                "confidence": _safe_float(mark.get("confidence") if isinstance(mark, dict) else mark.confidence),
                "source": _safe_metadata(mark.get("source") if isinstance(mark, dict) else mark.source),
                "valid": _safe_bool(mark.get("valid") if isinstance(mark, dict) else mark.valid),
                "reason": _safe_metadata(mark.get("reason") if isinstance(mark, dict) else mark.reason),
                "role_length": _safe_length(mark.get("role") if isinstance(mark, dict) else mark.role),
                "text_summary_length": _safe_length(mark.get("text_summary") if isinstance(mark, dict) else mark.text_summary),
            }
            for mark in list(result.marks or [])[:20]
        ],
    }


def _validate_provider_result(result: MarkProviderResult, binding: ScreenBinding) -> str | None:
    if not result.success or result.failure_code:
        return result.failure_code or "provider_failure"
    if not result.marks:
        return None
    if result.screen_id != binding.screen_id:
        return "stale_screen"
    if result.raw_screenshot_hash != binding.raw_screenshot_hash:
        return "hash_mismatch"
    if not result.provider_input_hash:
        return "missing_provider_hash"
    return None


def build_observation(
    *, screenshot: Any, current_app: str, marks: list[dict[str, Any]] | None = None,
    mark_providers: list[MarkProvider] | None = None,
    provider_hints: list[MarkProviderHint] | None = None,
    provider_timeout: float | None = None,
) -> Observation:
    """Build a screen observation with optional mock/provider marks.

    Provider fallback is intentionally safe: without marks, only screen id/hash
    are produced and mark-based actions cannot ground.
    """

    width = int(getattr(screenshot, "width", 0) or 0)
    height = int(getattr(screenshot, "height", 0) or 0)
    screenshot_b64 = getattr(screenshot, "base64_data", None)
    base_marks = list(marks or [])
    semantic_screen_id = build_semantic_screen_id(current_app=current_app, width=width, height=height)
    mark_topology_digest = build_mark_topology_digest(base_marks)
    perceptual_hash = compute_perceptual_hash(
        screenshot_b64,
        fallback_key=f"{semantic_screen_id}|{mark_topology_digest}",
    )
    raw_screenshot_hash = compute_raw_screenshot_hash(screenshot_b64)
    provisional_screen_id = build_screen_id(
        current_app=current_app,
        screenshot_b64=screenshot_b64,
        width=width,
        height=height,
        marks=base_marks,
    )

    binding = ScreenBinding(
        screen_id=provisional_screen_id,
        raw_screenshot_hash=raw_screenshot_hash,
        width=width,
        height=height,
        current_app=current_app,
        semantic_screen_id=semantic_screen_id,
        observation_epoch=0,
        mark_set_version=mark_topology_digest,
        perceptual_hash=perceptual_hash,
    )
    provider_summaries: list[dict[str, Any]] = []
    provider_marks: list[dict[str, Any]] = []
    for provider in mark_providers or []:
        try:
            hints_for_provider = (
                provider_hints or []
                if _provider_accepts_raw_hints(provider)
                else _redact_provider_hints(provider_hints or [])
            )
            result = provider.provide_marks(
                screenshot,
                binding,
                hints=hints_for_provider,
                timeout=provider_timeout,
            )
        except Exception as exc:
            provider_summaries.append(
                {
                    "provider": _safe_metadata(getattr(provider, "name", type(provider).__name__), default="unknown"),
                    "success": False,
                    "failure_code": "provider_error",
                    "message": _safe_metadata(type(exc).__name__),
                }
            )
            continue
        binding_error = _validate_provider_result(result, binding)
        if binding_error:
            summary = _summarize_provider_result(result)
            summary.update({"success": False, "failure_code": binding_error, "marks": []})
            provider_summaries.append(summary)
            continue
        provider_summaries.append(_summarize_provider_result(result))
        provider_marks.extend(_provider_result_to_marks(result))

    all_marks = base_marks + provider_marks
    screen_id = build_screen_id(
        current_app=current_app,
        screenshot_b64=screenshot_b64,
        width=width,
        height=height,
        marks=all_marks,
    )
    all_marks = [{**mark, "screen_id": mark.get("screen_id") or screen_id} for mark in all_marks]
    mark_topology_digest = build_mark_topology_digest(all_marks)
    registry = MarkRegistry.from_marks(screen_id, all_marks)
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
    return Observation(
        snapshot=snapshot,
        mark_registry=registry,
        mark_provider_observation={
            "providers": provider_summaries,
            "provider_count": len(mark_providers or []),
            "hint_count": len(provider_hints or []),
            "mark_count": len(registry.marks),
        },
    )
