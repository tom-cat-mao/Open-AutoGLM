"""Screen mark registry for harness-side GUI grounding."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from phone_agent.graph.context import sanitize_context_payload


SAFE_MARK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SAFE_METADATA_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
MAX_MARK_METADATA_CHARS = 32


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
        data["text_summary"] = sanitize_context_payload(self.text_summary or "", "message")
        return data


@dataclass(frozen=True)
class MarkRegistry:
    """Screen-bound mark lookup table."""

    screen_id: str
    marks: dict[str, Mark] = field(default_factory=dict)

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
        return cls(screen_id=screen_id, marks=parsed)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "MarkRegistry | None":
        if not isinstance(value, dict) or not value.get("screen_id"):
            return None
        marks_value = value.get("marks") or {}
        if isinstance(marks_value, dict):
            marks_iter = list(marks_value.values())
        else:
            marks_iter = list(marks_value or [])
        return cls.from_marks(str(value["screen_id"]), marks_iter)

    def to_dict(self) -> dict[str, Any]:
        return {"screen_id": self.screen_id, "marks": {key: mark.to_trace_dict() for key, mark in self.marks.items()}}

    def get(self, mark_id: str) -> Mark | None:
        return self.marks.get(str(mark_id))

    def trace_summary(self) -> dict[str, Any]:
        return {"screen_id": self.screen_id, "mark_count": len(self.marks), "marks": [m.to_trace_dict() for m in self.marks.values()]}

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


def build_screen_id(*, current_app: str, screenshot_b64: str | None, width: int, height: int) -> str:
    digest = hashlib.sha256(f"{current_app}|{width}x{height}|{screenshot_b64 or ''}".encode("utf-8")).hexdigest()
    return digest[:16]


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
