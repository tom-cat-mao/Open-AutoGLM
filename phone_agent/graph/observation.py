"""Observation builder for screen-bound harness metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phone_agent.config.app_registry import ForegroundAppObservation
from phone_agent.config.policy import DEFAULT_SAFETY_POLICY
from phone_agent.graph.marks import (
    MarkRegistry,
    build_mark_topology_digest,
    build_screen_id,
    build_semantic_screen_id,
    compute_perceptual_hash,
    compute_raw_screenshot_hash,
)
from phone_agent.graph.context import sanitize_context_payload
from phone_agent.graph.objects import (
    ObjectRegistry,
    ScreenStructure,
    StructureNode,
    build_composite_structure_digest,
    build_object_registry,
    build_structure_topology_digest,
    summarize_structures,
)
from phone_agent.grounding.provider import (
    MarkProvider,
    MarkProviderHint,
    MarkProviderResult,
    ScreenBinding,
)


def _safe_metadata(value: Any, *, default: str = "") -> str:
    safe = str(
        sanitize_context_payload(str(value or ""), "message", consumer="inject")
    ).strip()
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
    foreground_package: str | None
    foreground_activity: str | None
    foreground_canonical_id: str | None
    foreground_known: bool
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
            "foreground_package": self.foreground_package,
            "foreground_activity": self.foreground_activity,
            "foreground_canonical_id": self.foreground_canonical_id,
            "foreground_known": self.foreground_known,
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
    screen_structure: ScreenStructure | None = None
    screen_structures: list[ScreenStructure] = field(default_factory=list)
    object_registry: ObjectRegistry | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "mark_registry": self.mark_registry.to_dict(),
            "mark_provider_observation": self.mark_provider_observation,
            "screen_structure": (
                self.screen_structure.trace_summary() if self.screen_structure else None
            ),
            "screen_structures": [
                structure.trace_summary() for structure in self.screen_structures
            ],
            "object_registry": (
                self.object_registry.trace_summary() if self.object_registry else None
            ),
        }


def build_mark_provider_hints(
    *,
    task: str | None = None,
    reflection: str | None = None,
    action: dict[str, Any] | None = None,
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
        safe = str(
            sanitize_context_payload(str(value or ""), "message", consumer="inject")
        ).strip()
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
    if isinstance(action, dict) and len(hints) < max_hints:
        for key in ("text", "message", "target_text_hint"):
            text = _safe_text(action.get(key))
            if text:
                hints.append(
                    MarkProviderHint(
                        text=text,
                        source="action",
                        action=_safe_metadata(action.get("action")) or None,
                    )
                )
                break
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
        text = str(
            sanitize_context_payload(hint.text, "message", consumer="inject")
        ).strip()
        if not text:
            continue
        redacted.append(
            MarkProviderHint(
                text=text[:240],
                source=_safe_metadata(hint.source, default="hint"),
                role=str(
                    sanitize_context_payload(
                        hint.role or "", "message", consumer="inject"
                    )
                ).strip()[:240]
                or None,
                intent=str(
                    sanitize_context_payload(
                        hint.intent or "", "message", consumer="inject"
                    )
                ).strip()[:240]
                or None,
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
        confidence = (
            mark.get("confidence") if isinstance(mark, dict) else mark.confidence
        )
        role = mark.get("role") if isinstance(mark, dict) else mark.role
        text_summary = (
            mark.get("text_summary") if isinstance(mark, dict) else mark.text_summary
        )
        marks.append(
            {
                "mark_id": mark_id or f"{result.provider}_{index}",
                "bbox": bbox,
                "center": center,
                "source": source or result.provider,
                "confidence": 1.0 if confidence is None else confidence,
                "role": role,
                "text_summary": sanitize_context_payload(
                    text_summary or "", "message", consumer="inject"
                ),
            }
        )
    return marks


def _sanitize_mark_for_memory(mark: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(mark)
    if sanitized.get("password") is True:
        sanitized["text_summary"] = None
        return sanitized
    text_summary = sanitized.get("text_summary")
    if text_summary is None:
        text_summary = sanitized.get("text") or sanitized.get("label") or ""
    sanitized["text_summary"] = (
        str(sanitize_context_payload(text_summary, "message", consumer="inject"))
        or None
    )
    return sanitized


def _summarize_provider_result(result: MarkProviderResult) -> dict[str, Any]:
    """Return trace-safe provider metadata without raw hint or mark text."""

    summary = {
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
                if key
                in {
                    "source",
                    "has_text",
                    "text_length",
                    "has_role",
                    "role_length",
                    "has_intent",
                    "intent_length",
                    "action",
                }
            }
            for hint in list(result.hints or [])[:5]
            if isinstance(hint, dict)
        ],
        "marks": [
            {
                "mark_id": _safe_metadata(
                    mark.get("mark_id") if isinstance(mark, dict) else mark.mark_id
                ),
                "bbox": _safe_coordinate_list(
                    mark.get("bbox") if isinstance(mark, dict) else mark.bbox,
                    expected_len=4,
                ),
                "center": _safe_coordinate_list(
                    mark.get("center") if isinstance(mark, dict) else mark.center,
                    expected_len=2,
                ),
                "confidence": _safe_float(
                    mark.get("confidence")
                    if isinstance(mark, dict)
                    else mark.confidence
                ),
                "source": _safe_metadata(
                    mark.get("source") if isinstance(mark, dict) else mark.source
                ),
                "valid": _safe_bool(
                    mark.get("valid") if isinstance(mark, dict) else mark.valid
                ),
                "reason": _safe_metadata(
                    mark.get("reason") if isinstance(mark, dict) else mark.reason
                ),
                "role_length": _safe_length(
                    mark.get("role") if isinstance(mark, dict) else mark.role
                ),
                "text_summary_length": _safe_length(
                    mark.get("text_summary")
                    if isinstance(mark, dict)
                    else mark.text_summary
                ),
            }
            for mark in list(result.marks or [])[:20]
        ],
    }
    fallback_chain = _safe_fallback_chain((result.metadata or {}).get("fallback_chain"))
    hybrid_factory = _safe_hybrid_factory((result.metadata or {}).get("hybrid_factory"))
    parse_summary = _safe_parse_summary((result.metadata or {}).get("parse_summary"))
    metadata: dict[str, Any] = {}
    if fallback_chain:
        metadata["fallback_chain"] = fallback_chain
    if hybrid_factory:
        metadata["hybrid_factory"] = hybrid_factory
    if parse_summary:
        metadata["parse_summary"] = parse_summary
    if metadata:
        summary["metadata"] = metadata
    structure_summary = _safe_screen_structures_summary(_result_structure_dicts(result))
    if structure_summary:
        summary["screen_structures"] = structure_summary
        if structure_summary.get("structures"):
            summary["screen_structure"] = structure_summary["structures"][0]
    return summary


def _result_structure_dicts(result: MarkProviderResult) -> list[dict[str, Any]]:
    if isinstance(result.screen_structures, list) and result.screen_structures:
        raw_items = result.screen_structures
    elif isinstance(result.screen_structure, dict):
        raw_items = [result.screen_structure]
    else:
        raw_items = []
    structures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("source_provider") or item.get("provider") or result.provider),
            str(item.get("structure_kind") or "accessibility"),
            str(item.get("structure_digest") or item.get("topology_digest") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        structures.append(item)
    return structures


def _safe_screen_structures_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    if not values:
        return {}
    summaries = [
        _safe_screen_structure_summary(value)
        for value in values
        if isinstance(value, dict)
    ]
    summaries = [summary for summary in summaries if summary]
    if not summaries:
        return {}
    kind_counts: dict[str, int] = {}
    for summary in summaries:
        kind = summary.get("structure_kind") or "unknown"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    return {
        "structure_count": len(summaries),
        "kind_counts": kind_counts,
        "merge_order": [summary.get("structure_kind") for summary in summaries],
        "structures": summaries,
    }


def _safe_screen_structure_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "status": _safe_metadata(value.get("status"), default="ok"),
        "structure_kind": _safe_metadata(
            value.get("structure_kind"), default="accessibility"
        ),
        "source_provider": _safe_metadata(value.get("source_provider")),
        "confidence_tier": _safe_metadata(value.get("confidence_tier")),
        "structure_digest": _safe_metadata(
            value.get("structure_digest") or value.get("topology_digest")
        ),
        "node_count": _safe_int(value.get("node_count")),
        "topology_digest": _safe_metadata(value.get("topology_digest")),
        "root_node_id": _safe_metadata(value.get("root_node_id")),
    }


def _safe_fallback_chain(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "provider": _safe_metadata(item.get("provider"), default="unknown"),
                "success": bool(item.get("success")),
                "failure_code": _safe_metadata(item.get("failure_code")),
                "candidate_count": _safe_int(item.get("candidate_count")),
                "mark_count": _safe_int(item.get("mark_count")),
                "structure_count": _safe_int(item.get("structure_count")),
                "latency_ms": _safe_int(item.get("latency_ms")),
                "usable": bool(item.get("usable")),
                "skip_reason": _safe_enum(
                    item.get("skip_reason"),
                    {
                        "accessibility_dump_callback_missing",
                        "skip_accessibility_provider",
                    },
                ),
            }
        )
    return rows


def _safe_parse_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "xml_status": _safe_metadata(value.get("xml_status"), default="unknown"),
        "raw_node_count": _safe_int(value.get("raw_node_count")),
        "mark_count": _safe_int(value.get("mark_count")),
        "structure_node_count": _safe_int(value.get("structure_node_count")),
        "bounds_parse_fail_count": _safe_int(value.get("bounds_parse_fail_count")),
        "filtered_zero_area_count": _safe_int(value.get("filtered_zero_area_count")),
        "interactive_candidate_count": _safe_int(
            value.get("interactive_candidate_count")
        ),
    }


def _safe_hybrid_factory(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("hybrid_mode") is not True:
        return {}
    provider_order = value.get("provider_order")
    if not isinstance(provider_order, list):
        provider_order = []
    return {
        "hybrid_mode": True,
        "accessibility_child_enabled": value.get("accessibility_child_enabled") is True,
        "accessibility_child_skip_reason": _safe_enum(
            value.get("accessibility_child_skip_reason"),
            {"accessibility_dump_callback_missing", "skip_accessibility_provider"},
        ),
        "provider_order": [
            _safe_metadata(item, default="unknown") for item in provider_order[:8]
        ],
    }


def _safe_enum(value: Any, allowed: set[str]) -> str | None:
    item = str(value or "").strip()
    return item if item in allowed else None


def _validate_provider_result(
    result: MarkProviderResult, binding: ScreenBinding
) -> str | None:
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
    *,
    screenshot: Any,
    current_app: str,
    marks: list[dict[str, Any]] | None = None,
    mark_providers: list[MarkProvider] | None = None,
    provider_hints: list[MarkProviderHint] | None = None,
    provider_timeout: float | None = None,
    foreground: ForegroundAppObservation | None = None,
    observation_epoch: int = 0,
) -> Observation:
    """Build a screen observation with optional mock/provider marks.

    Provider fallback is intentionally safe: without marks, only screen id/hash
    are produced and mark-based actions cannot ground.
    """

    width = int(getattr(screenshot, "width", 0) or 0)
    height = int(getattr(screenshot, "height", 0) or 0)
    screenshot_b64 = getattr(screenshot, "base64_data", None)
    base_marks = list(marks or [])
    semantic_screen_id = build_semantic_screen_id(
        current_app=current_app, width=width, height=height
    )
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
        observation_epoch=observation_epoch,
        mark_set_version=mark_topology_digest,
        perceptual_hash=perceptual_hash,
    )
    provider_summaries: list[dict[str, Any]] = []
    provider_marks: list[dict[str, Any]] = []
    provider_structures: list[ScreenStructure] = []
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
                    "provider": _safe_metadata(
                        getattr(provider, "name", type(provider).__name__),
                        default="unknown",
                    ),
                    "success": False,
                    "failure_code": "provider_error",
                    "message": _safe_metadata(type(exc).__name__),
                }
            )
            continue
        binding_error = _validate_provider_result(result, binding)
        if binding_error:
            summary = _summarize_provider_result(result)
            summary.update(
                {"success": False, "failure_code": binding_error, "marks": []}
            )
            provider_summaries.append(summary)
            continue
        provider_summaries.append(_summarize_provider_result(result))
        provider_marks.extend(_provider_result_to_marks(result))
        for structure_dict in _result_structure_dicts(result):
            structure = _screen_structure_from_dict(structure_dict)
            if structure is not None:
                provider_structures.append(structure)

    all_marks = [
        _sanitize_mark_for_memory(mark) for mark in base_marks + provider_marks
    ]
    screen_id = build_screen_id(
        current_app=current_app,
        screenshot_b64=screenshot_b64,
        width=width,
        height=height,
        marks=all_marks,
    )
    all_marks = [
        {**mark, "screen_id": mark.get("screen_id") or screen_id} for mark in all_marks
    ]
    mark_topology_digest = build_mark_topology_digest(all_marks)
    registry = MarkRegistry.from_marks(screen_id, all_marks)
    registry = MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id=semantic_screen_id,
        observation_epoch=observation_epoch,
        mark_set_version=registry.mark_set_version or mark_topology_digest,
        perceptual_hash=perceptual_hash,
        raw_screenshot_hash=raw_screenshot_hash,
    )
    snapshot = ScreenSnapshot(
        screen_id=screen_id,
        screen_hash=raw_screenshot_hash,
        current_app=current_app,
        foreground_package=foreground.package_name if foreground else None,
        foreground_activity=foreground.activity_name if foreground else None,
        foreground_canonical_id=foreground.canonical_id if foreground else None,
        foreground_known=foreground.known if foreground else False,
        width=width,
        height=height,
        semantic_screen_id=semantic_screen_id,
        observation_epoch=observation_epoch,
        mark_set_version=registry.mark_set_version,
        perceptual_hash=perceptual_hash,
        raw_screenshot_hash=raw_screenshot_hash,
    )
    bound_structures: list[ScreenStructure] = []
    seen_structure_keys: set[tuple[str, str, str]] = set()
    for structure in sorted(
        provider_structures,
        key=lambda item: (
            1 if item.structure_kind == "visual" else 0,
            item.source_provider or "",
            item.structure_digest or item.topology_digest or "",
        ),
    ):
        bound = structure.with_binding(
            screen_id=screen_id,
            semantic_screen_id=semantic_screen_id,
            mark_set_version=registry.mark_set_version,
        )
        key = (
            bound.source_provider or "",
            bound.structure_kind,
            bound.structure_digest or bound.topology_digest or "",
        )
        if key in seen_structure_keys:
            continue
        seen_structure_keys.add(key)
        bound_structures.append(bound)
    screen_structure = bound_structures[0] if bound_structures else None
    object_registry = build_object_registry(
        screen_id=screen_id,
        structure=bound_structures,
        mark_registry=registry,
    )
    composite_structure_digest = build_composite_structure_digest(bound_structures)
    if screen_structure is not None:
        binding = ScreenBinding(
            screen_id=screen_id,
            raw_screenshot_hash=raw_screenshot_hash,
            width=width,
            height=height,
            current_app=current_app,
            semantic_screen_id=semantic_screen_id,
            observation_epoch=observation_epoch,
            mark_set_version=registry.mark_set_version,
            perceptual_hash=perceptual_hash,
            structure_topology_digest=composite_structure_digest,
            object_set_version=object_registry.object_set_version,
        )
    return Observation(
        snapshot=snapshot,
        mark_registry=registry,
        mark_provider_observation={
            "providers": provider_summaries,
            "provider_count": len(mark_providers or []),
            "hint_count": len(provider_hints or []),
            "mark_count": len(registry.marks),
            "screen_structure_summary": summarize_structures(bound_structures),
            "object_registry_summary": object_registry.trace_summary(),
        },
        screen_structure=screen_structure,
        screen_structures=bound_structures,
        object_registry=object_registry,
    )


def _screen_structure_from_dict(value: dict[str, Any]) -> ScreenStructure | None:
    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, dict):
        return None
    structure_kind = _safe_metadata(
        value.get("structure_kind"), default="accessibility"
    )
    if structure_kind not in {"accessibility", "visual"}:
        structure_kind = "accessibility"
    source_provider = (
        _safe_metadata(value.get("source_provider") or value.get("provider")) or None
    )
    confidence_tier = _safe_metadata(value.get("confidence_tier")) or (
        "weak" if structure_kind == "visual" else "strong"
    )
    nodes: dict[str, StructureNode] = {}
    for node_id, item in raw_nodes.items():
        if not isinstance(item, dict):
            continue
        bounds_value = item.get("bounds")
        bounds = None
        if (
            isinstance(bounds_value, list)
            and len(bounds_value) == 4
            and all(isinstance(v, int) for v in bounds_value)
        ):
            bounds = (
                bounds_value[0],
                bounds_value[1],
                bounds_value[2],
                bounds_value[3],
            )
        child_ids = [
            str(child)
            for child in item.get("child_ids") or []
            if isinstance(child, str)
        ]
        nodes[str(node_id)] = StructureNode(
            node_id=str(item.get("node_id") or node_id),
            path=str(item.get("path") or ""),
            parent_id=(
                item.get("parent_id")
                if isinstance(item.get("parent_id"), str)
                else None
            ),
            child_ids=child_ids,
            depth=_safe_int(item.get("depth"), maximum=10_000),
            bounds=bounds,
            role=_safe_metadata(item.get("role")) or None,
            class_name=_safe_metadata(item.get("class_name")) or None,
            resource_id_hash=_safe_metadata(item.get("resource_id_hash")) or None,
            text_summary=_safe_metadata(item.get("text_summary")) or None,
            content_desc_summary=_safe_metadata(item.get("content_desc_summary"))
            or None,
            clickable=bool(item.get("clickable")),
            focusable=bool(item.get("focusable")),
            focused=bool(item.get("focused")),
            checkable=bool(item.get("checkable")),
            checked=bool(item.get("checked")),
            scrollable=bool(item.get("scrollable")),
            enabled=item.get("enabled") is not False,
            visible=item.get("visible") is not False,
            structure_kind=structure_kind,
            source_provider=source_provider,
            confidence_tier=_safe_metadata(item.get("confidence_tier"))
            or confidence_tier,
            node_provenance=_safe_metadata(item.get("node_provenance")) or None,
            visual_order=(
                _safe_int(item.get("visual_order"), maximum=10_000)
                if item.get("visual_order") is not None
                else None
            ),
            confidence=_safe_float(item.get("confidence")),
            sensitivity_tags=_safe_sensitivity_tags(item.get("sensitivity_tags")),
        )
    if not nodes:
        return None
    return ScreenStructure(
        screen_id=_safe_metadata(value.get("screen_id")),
        semantic_screen_id=_safe_metadata(value.get("semantic_screen_id")) or None,
        mark_set_version=_safe_metadata(value.get("mark_set_version")) or None,
        topology_digest=_safe_metadata(value.get("topology_digest"))
        or build_structure_topology_digest(nodes),
        status=_safe_metadata(value.get("status"), default="ok"),
        nodes=nodes,
        root_node_id=_safe_metadata(value.get("root_node_id")) or None,
        structure_kind=structure_kind,
        source_provider=source_provider,
        confidence_tier=confidence_tier,
        structure_version=_safe_metadata(value.get("structure_version")) or None,
        structure_digest=_safe_metadata(
            value.get("structure_digest") or value.get("topology_digest")
        )
        or build_structure_topology_digest(nodes),
    )


def _safe_sensitivity_tags(value: Any) -> list[str]:
    allowed = DEFAULT_SAFETY_POLICY.semantic_tags
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = str(item or "").strip().casefold()
        if tag in allowed and tag not in tags:
            tags.append(tag)
    return tags
