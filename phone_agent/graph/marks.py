"""Screen mark registry for harness-side GUI grounding."""

from __future__ import annotations

import hashlib
import base64
import re
from dataclasses import asdict, dataclass, field, replace
from io import BytesIO
from typing import Any

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.config.policy import (
    DEFAULT_VERIFICATION_POLICY,
    LOCATE_INHERIT_PHASH_MAX_DISTANCE,
)


SAFE_MARK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SAFE_METADATA_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
MAX_MARK_METADATA_CHARS = 32
PERCEPTUAL_HASH_THRESHOLD = int(
    DEFAULT_VERIFICATION_POLICY.value("perceptual_hash_max_distance")
)
MARK_CONFIDENCE_THRESHOLD = DEFAULT_VERIFICATION_POLICY.value("mark_min_confidence")

# D1: accessibility-origin mark sources form the stable screen-structure
# projection. Only these sources may feed the screen_id topology component;
# provider/locate marks (la_*, locate_N) never enter screen identity.
ACCESSIBILITY_MARK_SOURCES = ("uiautomator", "accessibility_tree")


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
    password: bool = False

    def to_trace_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text_summary"] = (
            None
            if self.password
            else sanitize_context_payload(
                self.text_summary or "", "message", consumer="checkpoint"
            )
        )
        return data

    def to_state_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text_summary"] = (
            None
            if self.password
            else _sanitize_mark_text_for_prompt(self.text_summary or "")
        )
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
    def from_marks(
        cls, screen_id: str, marks: list[dict[str, Any] | Mark] | None
    ) -> "MarkRegistry":
        parsed: dict[str, Mark] = {}
        for index, item in enumerate(marks or [], start=1):
            try:
                mark = (
                    item
                    if isinstance(item, Mark)
                    else _coerce_mark(screen_id, item, index)
                )
            except (TypeError, ValueError):
                continue
            if mark.screen_id == screen_id:
                parsed[mark.mark_id] = mark
        mark_set_version = build_mark_set_version(parsed)
        return cls(
            screen_id=screen_id,
            marks=parsed,
            semantic_screen_id=str(marks[0].get("semantic_screen_id"))
            if marks
            and isinstance(marks[0], dict)
            and marks[0].get("semantic_screen_id")
            else None,
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
            semantic_screen_id=value.get("semantic_screen_id")
            or registry.semantic_screen_id,
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
            "marks": {key: mark.to_state_dict() for key, mark in self.marks.items()},
        }

    def get(self, mark_id: str) -> Mark | None:
        return self.marks.get(str(mark_id))

    def with_extra_marks(self, extra: list[dict[str, Any] | Mark]) -> "MarkRegistry":
        """Merge additional marks onto the SAME screen snapshot.

        F1 locate: the visual provider is queried against the current
        screenshot, so the screen binding (``screen_id`` / ``perceptual_hash`` /
        ``raw_screenshot_hash``) is unchanged — only ``mark_set_version`` is
        recomputed (P0 #9: hash binding must not mismatch after merging). Marks
        bound to a different screen are dropped fail-closed. ``mark_id``
        generation is the caller's job (``locate_N`` from the state counter);
        this method never invents ids.
        """

        if not extra:
            return self
        merged = dict(self.marks)
        for index, item in enumerate(extra, start=1):
            try:
                mark = item if isinstance(item, Mark) else _coerce_mark(
                    self.screen_id, item, index
                )
            except (TypeError, ValueError):
                continue
            if mark.screen_id != self.screen_id:
                continue
            merged[mark.mark_id] = mark
        return MarkRegistry(
            screen_id=self.screen_id,
            marks=merged,
            semantic_screen_id=self.semantic_screen_id,
            observation_epoch=self.observation_epoch,
            mark_set_version=build_mark_set_version(merged),
            perceptual_hash=self.perceptual_hash,
            raw_screenshot_hash=self.raw_screenshot_hash,
        )

    def with_inherited_locate_marks(
        self, extra: list[dict[str, Any] | Mark], *, previous: "MarkRegistry"
    ) -> "MarkRegistry":
        """F-A/D2: merge locate_N marks inherited from the previous observation.

        Same-screen marks (exact ``screen_id`` match, the common case after D1
        made screen_id stable) merge exactly like ``with_extra_marks``. When the
        screen_id differs but the previous screen is still the same physical
        page (an accessibility-tree jitter of one node still flips the D1
        topology digest), a mark survives only when the relaxed same-page gate
        passes:

        ``semantic_screen_id`` equal AND (ax structure digest equal OR
        perceptual-hash hamming distance <= LOCATE_INHERIT_PHASH_MAX_DISTANCE).

        The ax structure digest compares the mark-topology digest over
        accessibility-origin marks only (never la_*/provider marks); an
        empty-vs-empty digest never counts as equal, so the gate stays
        fail-closed on screens with no ax marks. Surviving marks are re-bound to
        THIS registry's screen_id and ``mark_set_version`` is recomputed once.
        Marks from any other screen are still dropped fail-closed.
        """

        if not extra:
            return self
        relaxed_ok = False
        if self.screen_id != previous.screen_id:
            semantic_ok = bool(
                self.semantic_screen_id
                and previous.semantic_screen_id
                and self.semantic_screen_id == previous.semantic_screen_id
            )
            if semantic_ok:
                prev_ax = build_ax_mark_digest(previous.marks)
                new_ax = build_ax_mark_digest(self.marks)
                ax_equal = prev_ax is not None and prev_ax == new_ax
                distance = hash_hamming_distance(
                    self.perceptual_hash, previous.perceptual_hash
                )
                p_hash_ok = (
                    distance is not None
                    and distance <= LOCATE_INHERIT_PHASH_MAX_DISTANCE
                )
                relaxed_ok = ax_equal or p_hash_ok
        merged = dict(self.marks)
        for index, item in enumerate(extra, start=1):
            try:
                mark = item if isinstance(item, Mark) else _coerce_mark(
                    self.screen_id, item, index
                )
            except (TypeError, ValueError):
                continue
            if mark.screen_id == self.screen_id:
                merged[mark.mark_id] = mark
            elif relaxed_ok and mark.screen_id == previous.screen_id:
                merged[mark.mark_id] = replace(mark, screen_id=self.screen_id)
        return MarkRegistry(
            screen_id=self.screen_id,
            marks=merged,
            semantic_screen_id=self.semantic_screen_id,
            observation_epoch=self.observation_epoch,
            mark_set_version=build_mark_set_version(merged),
            perceptual_hash=self.perceptual_hash,
            raw_screenshot_hash=self.raw_screenshot_hash,
        )

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

    def prompt_block(self, lang: str = "cn", excluded_mark_ids=None) -> str:
        """Render the marks block, optionally excluding invalidated mark ids.

        S4: ``excluded_mark_ids`` drops invalidated ``locate_*`` marks from the
        block the model sees, so a wrong box is never offered as a target
        again. Rendering only — the registry (and its D2 inheritance/version
        semantics) is untouched.
        """

        excluded = {str(mark_id) for mark_id in (excluded_mark_ids or [])}
        if not self.marks:
            return ""
        title = "** Screen Marks (use target_mark_id; do not guess coordinates) **"
        if lang != "en":
            title = "** 屏幕标记（使用 target_mark_id，不要猜坐标） **"
        rows = []
        for mark in self.marks.values():
            if str(mark.mark_id) in excluded:
                continue
            summary = mark.to_prompt_dict()
            rows.append(
                f"- {mark.mark_id}: role={summary.get('role') or 'unknown'} "
                f"source={summary.get('source')} confidence={summary.get('confidence')} "
                f"bbox={list(mark.bbox)} center={list(mark.center)} "
                f"position={_mark_position_label(mark)} "
                f"text_summary={summary.get('text_summary')}"
            )
        if not rows:
            return ""
        return title + "\n" + "\n".join(rows)


def build_screen_id(
    *,
    current_app: str,
    screenshot_b64: str | None,
    width: int,
    height: int,
    marks: list[dict[str, Any] | Mark] | None = None,
) -> str:
    semantic_id = build_semantic_screen_id(
        current_app=current_app, width=width, height=height
    )
    topology_digest = build_mark_topology_digest(marks)
    digest = hashlib.sha256(
        f"{semantic_id}|"
        f"{compute_perceptual_hash(screenshot_b64, fallback_key=f'{semantic_id}|{topology_digest}')}|{topology_digest}".encode(
            "utf-8"
        )
    ).hexdigest()
    return digest[:16]


def build_semantic_screen_id(*, current_app: str, width: int, height: int) -> str:
    return hashlib.sha256(
        f"{current_app}|{width}x{height}".encode("utf-8")
    ).hexdigest()[:16]


def compute_raw_screenshot_hash(screenshot_b64: str | None) -> str:
    return hashlib.sha256((screenshot_b64 or "").encode("utf-8")).hexdigest()[:16]


def compute_perceptual_hash(
    screenshot_b64: str | None, *, fallback_key: str | None = None
) -> str:
    if not screenshot_b64:
        return hashlib.sha256((fallback_key or "").encode("utf-8")).hexdigest()[:16]
    try:
        from PIL import Image  # type: ignore

        raw = base64.b64decode(screenshot_b64)
        image = Image.open(BytesIO(raw)).convert("L").resize((8, 8))
        values = list(image.getdata())
        avg = sum(values) / len(values)
        bits = "".join("1" if value >= avg else "0" for value in values)
        return f"{int(bits, 2):016x}"
    except Exception:
        # Raw screenshot hash is audit-only. If image decoding/Pillow is unavailable,
        # fall back to deterministic semantic/layout inputs supplied by the caller
        # instead of reintroducing pixel-sensitive raw screenshot binding.
        return hashlib.sha256(
            (fallback_key or "perceptual_hash_unavailable").encode("utf-8")
        ).hexdigest()[:16]


def build_mark_topology_digest(
    marks: list[dict[str, Any] | Mark] | dict[str, Any] | None,
) -> str:
    if isinstance(marks, dict):
        iterable = (
            list((marks.get("marks") or {}).values())
            if "marks" in marks
            else list(marks.values())
        )
    else:
        iterable = list(marks or [])
    rows: list[str] = []
    for item in iterable:
        if isinstance(item, Mark):
            rows.append(
                f"{item.mark_id}:{tuple(round(v, 1) for v in item.bbox)}:{item.role or ''}:{item.source}"
            )
        elif isinstance(item, dict):
            bbox = item.get("bbox") or item.get("bounds") or item.get("rect") or []
            rows.append(
                f"{item.get('mark_id') or item.get('id') or ''}:{bbox}:{item.get('role') or ''}:{item.get('source') or ''}"
            )
    return hashlib.sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def build_ax_mark_digest(
    marks: dict[str, Mark] | list[dict[str, Any] | Mark] | None,
) -> str | None:
    """Return the D1 screen-structure digest over accessibility-origin marks.

    Only marks whose ``source`` is an accessibility source (uiautomator /
    accessibility_tree) are folded in; provider/locate marks (la_*, locate_N,
    fake, ...) are excluded. Returns None when no ax mark is present so an
    empty-vs-empty comparison can never authorize a merge (fail-closed).
    """

    iterable = list(marks.values()) if isinstance(marks, dict) else list(marks or [])
    ax = [
        item
        for item in iterable
        if (_mark_source(item) or "") in ACCESSIBILITY_MARK_SOURCES
    ]
    if not ax:
        return None
    return build_mark_topology_digest(ax)


def _mark_source(item: Mark | dict[str, Any]) -> str | None:
    if isinstance(item, Mark):
        return item.source
    return item.get("source") if isinstance(item, dict) else None


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
    center = (
        _coerce_point(center_value)
        if center_value is not None
        else ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    )
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
        text_summary=None
        if item.get("password") is True
        else str(
            item.get("text_summary") or item.get("text") or item.get("label") or ""
        )
        or None,
        password=item.get("password") is True,
    )


def _mark_position_label(mark: Mark) -> str:
    x, y = mark.center
    if y < 160:
        vertical = "top"
    elif y > 840:
        vertical = "bottom"
    else:
        vertical = "middle"
    if x < 250:
        horizontal = "left"
    elif x > 750:
        horizontal = "right"
    else:
        horizontal = "center"
    width = max(0.0, mark.bbox[2] - mark.bbox[0])
    height = max(0.0, mark.bbox[3] - mark.bbox[1])
    shape = "wide" if width >= 350 and width >= height * 3 else "compact"
    return f"{vertical}-{horizontal}-{shape}"


def _sanitize_mark_text_for_prompt(value: str) -> str:
    return str(
        sanitize_context_payload(value or "", "text_summary", consumer="inject")
    ).strip()


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
