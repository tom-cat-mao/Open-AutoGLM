"""Screen mark registry for harness-side GUI grounding."""

from __future__ import annotations

import hashlib
import base64
import re
from io import BytesIO
from dataclasses import asdict, dataclass, field
from typing import Any

from phone_agent.graph.context import sanitize_context_payload


SAFE_MARK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SAFE_METADATA_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
MAX_MARK_METADATA_CHARS = 32
PERCEPTUAL_HASH_THRESHOLD = 8
MARK_CONFIDENCE_THRESHOLD = 0.3


@dataclass(frozen=True)
class Mark:
    """A trace-safe GUI mark bound to one screen snapshot."""

    mark_id: str
    screen_id: str
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    source: str = "mock"
    confidence: float = 1.0
    role: str | None = None
    text_summary: str | None = None

    def to_trace_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text_summary"] = sanitize_context_payload(self.text_summary or "", "message", consumer="checkpoint")
        return data


@dataclass(frozen=True)
class MarkRegistry:
    """Screen-bound mark lookup table."""

    screen_id: str
    marks: dict[str, Mark] = field(default_factory=dict)
    semantic_screen_id: str | None = None
    observation_epoch: int = 0
    mark_set_version: str | None = None
    perceptual_hash: str | None = None
    raw_screenshot_hash: str | None = None

    @classmethod
    def from_marks(cls, screen_id: str, marks: list[dict[str, Any] | Mark] | None) -> "MarkRegistry":
        parsed: dict[str, Mark] = {}
        for index, item in enumerate(marks or [], start=1):
            try:
                mark = item if isinstance(item, Mark) else _coerce_mark(screen_id, item, index)
            except (TypeError, ValueError):
                continue
            if mark.screen_id == screen_id:
                parsed[mark.mark_id] = mark
        mark_set_version = build_mark_set_version(parsed)
        return cls(
            screen_id=screen_id,
            marks=parsed,
            semantic_screen_id=str(marks[0].get("semantic_screen_id")) if marks and isinstance(marks[0], dict) and marks[0].get("semantic_screen_id") else None,
            mark_set_version=mark_set_version,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "MarkRegistry | None":
        if not isinstance(value, dict) or not value.get("screen_id"):
            return None
        marks_value = value.get("marks") or {}
        if isinstance(marks_value, dict):
            marks_iter = list(marks_value.values())
        else:
            marks_iter = list(marks_value or [])
        registry = cls.from_marks(str(value["screen_id"]), marks_iter)
        return cls(
            screen_id=registry.screen_id,
            marks=registry.marks,
            semantic_screen_id=value.get("semantic_screen_id") or registry.semantic_screen_id,
            observation_epoch=int(value.get("observation_epoch") or 0),
            mark_set_version=value.get("mark_set_version") or registry.mark_set_version,
            perceptual_hash=value.get("perceptual_hash"),
            raw_screenshot_hash=value.get("raw_screenshot_hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "observation_epoch": self.observation_epoch,
            "mark_set_version": self.mark_set_version,
            "perceptual_hash": self.perceptual_hash,
            "raw_screenshot_hash": self.raw_screenshot_hash,
            "marks": {key: mark.to_trace_dict() for key, mark in self.marks.items()},
        }

    def get(self, mark_id: str) -> Mark | None:
        return self.marks.get(str(mark_id))

    def trace_summary(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "semantic_screen_id": self.semantic_screen_id,
            "observation_epoch": self.observation_epoch,
            "mark_set_version": self.mark_set_version,
            "perceptual_hash": self.perceptual_hash,
            "mark_count": len(self.marks),
            "marks": [m.to_trace_dict() for m in self.marks.values()],
        }

    def prompt_block(self, lang: str = "cn") -> str:
        if not self.marks:
            return ""
        title = "** Screen Marks (use target_mark_id; do not guess coordinates) **"
        if lang != "en":
            title = "** 屏幕标记（使用 target_mark_id，不要猜坐标） **"
        rows = []
        for mark in self.marks.values():
            summary = mark.to_trace_dict()
            rows.append(
                f"- {mark.mark_id}: role={summary.get('role') or 'unknown'} "
                f"source={summary.get('source')} confidence={summary.get('confidence')} "
                f"text_summary={summary.get('text_summary')}"
            )
        return title + "\n" + "\n".join(rows)


def build_screen_id(
    *, current_app: str, screenshot_b64: str | None, width: int, height: int, marks: list[dict[str, Any] | Mark] | None = None
) -> str:
    semantic_id = build_semantic_screen_id(current_app=current_app, width=width, height=height)
    topology_digest = build_mark_topology_digest(marks)
    digest = hashlib.sha256(
        f"{semantic_id}|"
        f"{compute_perceptual_hash(screenshot_b64, fallback_key=f'{semantic_id}|{topology_digest}')}|{topology_digest}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def build_semantic_screen_id(*, current_app: str, width: int, height: int) -> str:
    return hashlib.sha256(f"{current_app}|{width}x{height}".encode("utf-8")).hexdigest()[:16]


def compute_raw_screenshot_hash(screenshot_b64: str | None) -> str:
    return hashlib.sha256((screenshot_b64 or "").encode("utf-8")).hexdigest()[:16]


def compute_perceptual_hash(screenshot_b64: str | None, *, fallback_key: str | None = None) -> str:
    if not screenshot_b64:
        return hashlib.sha256((fallback_key or "").encode("utf-8")).hexdigest()[:16]
    try:
        from PIL import Image  # type: ignore

        raw = base64.b64decode(screenshot_b64)
        image = Image.open(BytesIO(raw)).convert("L").resize((8, 8))
        values = list(image.getdata())
        avg = sum(values) / len(values)
        bits = ''.join('1' if value >= avg else '0' for value in values)
        return f"{int(bits, 2):016x}"
    except Exception:
        # Raw screenshot hash is audit-only. If image decoding/Pillow is unavailable,
        # fall back to deterministic semantic/layout inputs supplied by the caller
        # instead of reintroducing pixel-sensitive raw screenshot binding.
        return hashlib.sha256((fallback_key or "perceptual_hash_unavailable").encode("utf-8")).hexdigest()[:16]


def build_mark_topology_digest(marks: list[dict[str, Any] | Mark] | dict[str, Any] | None) -> str:
    if isinstance(marks, dict):
        iterable = list((marks.get("marks") or {}).values()) if "marks" in marks else list(marks.values())
    else:
        iterable = list(marks or [])
    rows: list[str] = []
    for item in iterable:
        if isinstance(item, Mark):
            rows.append(f"{item.mark_id}:{tuple(round(v, 1) for v in item.bbox)}:{item.role or ''}:{item.source}")
        elif isinstance(item, dict):
            bbox = item.get("bbox") or item.get("bounds") or item.get("rect") or []
            rows.append(f"{item.get('mark_id') or item.get('id') or ''}:{bbox}:{item.get('role') or ''}:{item.get('source') or ''}")
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_mark_set_version(marks: dict[str, Mark]) -> str:
    return build_mark_topology_digest(list(marks.values()))


def hash_hamming_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def _coerce_mark(screen_id: str, item: dict[str, Any], index: int) -> Mark:
    mark_id = _safe_mark_id(item.get("mark_id") or item.get("id") or f"m{index}")
    bbox_value = item.get("bbox") or item.get("bounds") or item.get("rect")
    bbox = _coerce_bbox(bbox_value)
    center_value = item.get("center")
    center = _coerce_point(center_value) if center_value is not None else ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    confidence = item.get("confidence", 1.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = 0.0
    return Mark(
        mark_id=mark_id,
        screen_id=str(item.get("screen_id") or screen_id),
        bbox=bbox,
        center=center,
        source=_safe_mark_metadata(item.get("source") or "mock", default="unknown"),
        confidence=max(0.0, min(float(confidence), 1.0)),
        role=_safe_mark_metadata(item.get("role"), default="") or None,
        text_summary=str(item.get("text_summary") or item.get("text") or item.get("label") or "") or None,
    )


def _safe_mark_id(value: Any) -> str:
    mark_id = str(value or "")
    if not SAFE_MARK_ID_RE.fullmatch(mark_id):
        raise ValueError("mark_id contains unsafe characters")
    return mark_id


def _safe_mark_metadata(value: Any, *, default: str) -> str:
    if value is None:
        return default
    safe = SAFE_METADATA_RE.sub("_", str(value)).strip("_")[:MAX_MARK_METADATA_CHARS]
    return safe or default


def _coerce_bbox(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("mark bbox must be [x1, y1, x2, y2]")
    coords = tuple(_coerce_relative_number(v) for v in value)
    x1, y1, x2, y2 = coords
    if x2 < x1 or y2 < y1:
        raise ValueError("mark bbox must be ordered")
    return coords


def _coerce_point(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("mark center must be [x, y]")
    return (_coerce_relative_number(value[0]), _coerce_relative_number(value[1]))


def _coerce_relative_number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("mark coordinate must be numeric")
    coordinate = float(value)
    if coordinate < 0 or coordinate > 1000:
        raise ValueError("mark coordinate must be in 0-1000 relative range")
    return coordinate
