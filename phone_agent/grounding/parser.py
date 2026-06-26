"""Parser for LocateAnything bbox responses.

Supports two formats:
  - Native (training): <box><x1><y1><x2><y2></box>
  - Space-separated:   <box>x1 y1 x2 y2</box>
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class GroundingParseError(ValueError):
    """Stable parser failure for trace/eval classification."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedBox:
    bbox: list[int]
    center: list[int]
    area: int
    valid: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class ParsedBoxSet:
    candidates: list[ParsedBox]
    valid_candidates: list[ParsedBox]
    candidate_count: int


# Native LocateAnything format: <box><173><446><316><473></box>
ANGLE_BOX_RE = re.compile(
    r"<box>\s*<(-?\d+(?:\.\d+)?)>\s*<(-?\d+(?:\.\d+)?)>\s*"
    r"<(-?\d+(?:\.\d+)?)>\s*<(-?\d+(?:\.\d+)?)>\s*</box>",
    re.IGNORECASE,
)

# Space-separated / bracketed format: <box>173 446 316 473</box> or <box>[173, 446, 316, 473]</box>
SPACE_BOX_RE = re.compile(
    r"<box>\s*\[?\s*(-?\d+(?:\.\d+)?)\s*[,\s]+(-?\d+(?:\.\d+)?)\s*[,\s]+"
    r"(-?\d+(?:\.\d+)?)\s*[,\s]+(-?\d+(?:\.\d+)?)\s*\]?\s*</box>",
    re.IGNORECASE,
)

def _extract_boxes(text: str) -> list[tuple[int, int, int, int]]:
    """Extract bbox candidates from model output, preferring native format."""
    boxes: list[tuple[int, int, int, int]] = []

    for regex in (ANGLE_BOX_RE, SPACE_BOX_RE):
        for match in regex.finditer(text):
            coords = tuple(int(round(float(v))) for v in match.groups())
            if coords not in boxes:
                boxes.append(coords)

    for coords in _extract_json_boxes(text):
        if coords not in boxes:
            boxes.append(coords)

    return boxes


def _extract_json_boxes(text: str) -> list[tuple[int, int, int, int]]:
    snippets = []
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        snippets.append(stripped)
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    snippets.extend(snippet.strip() for snippet in fenced)
    boxes: list[tuple[int, int, int, int]] = []
    for snippet in snippets:
        try:
            payload = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        for item in _json_box_values(payload):
            try:
                coords = tuple(int(round(float(value))) for value in item)
            except (TypeError, ValueError):
                continue
            boxes.append(coords)
    return boxes


def _json_box_values(value: object) -> list[list[object]]:
    if isinstance(value, dict):
        for key in ("bbox", "box"):
            item = value.get(key)
            if isinstance(item, list) and len(item) == 4:
                return [item]
        boxes = value.get("boxes")
        if isinstance(boxes, list):
            return [item for item in boxes if isinstance(item, list) and len(item) == 4]
        return []
    if isinstance(value, list) and len(value) == 4 and not any(isinstance(item, (list, dict)) for item in value):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, list) and len(item) == 4]
    return []


def _validate_box(
    coords: tuple[int, int, int, int],
    *,
    min_area: int,
    max_area_ratio: float,
    normalize_order: bool,
) -> ParsedBox:
    if any(value < 0 or value > 1000 for value in coords):
        raise GroundingParseError("out_of_range", "bbox coordinates must be in 0-1000")
    x1, y1, x2, y2 = coords
    if normalize_order:
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
    elif x2 <= x1 or y2 <= y1:
        raise GroundingParseError("bad_order", "bbox coordinates must be ordered")
    width = x2 - x1
    height = y2 - y1
    area = width * height
    if width <= 0 or height <= 0 or area < min_area:
        raise GroundingParseError("too_small", "bbox area is too small")
    if area > int(1_000_000 * max_area_ratio):
        raise GroundingParseError("too_large", "bbox area is too large")
    bbox = [x1, y1, x2, y2]
    center = [int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))]
    return ParsedBox(bbox=bbox, center=center, area=area)


def parse_box_response(
    text: str,
    *,
    min_area: int = 4,
    max_area_ratio: float = 0.95,
    normalize_order: bool = True,
) -> ParsedBox:
    """Parse exactly one valid LocateAnything bbox in normalized 0-1000 coordinates."""

    parsed_set = parse_box_candidates(
        text,
        min_area=min_area,
        max_area_ratio=max_area_ratio,
        normalize_order=normalize_order,
    )
    if not parsed_set.valid_candidates:
        if parsed_set.candidates:
            raise GroundingParseError(
                parsed_set.candidates[0].reason or "no_candidate",
                "no valid bbox in grounding output",
            )
        raise GroundingParseError("no_candidate", "no valid bbox in grounding output")
    if len(parsed_set.valid_candidates) > 1:
        raise GroundingParseError("grounding_ambiguous", "multiple valid bboxes in grounding output")
    return parsed_set.valid_candidates[0]


def parse_box_candidates(
    text: str,
    *,
    min_area: int = 4,
    max_area_ratio: float = 0.95,
    normalize_order: bool = True,
) -> ParsedBoxSet:
    """Parse all LocateAnything bbox candidates and classify valid candidates."""

    if not isinstance(text, str) or not text.strip():
        raise GroundingParseError("empty_output", "grounding output is empty")

    candidates = _extract_boxes(text)
    if not candidates:
        raise GroundingParseError("invalid_format", "missing <box>x1 y1 x2 y2</box>")

    parsed: list[ParsedBox] = []
    valid: list[ParsedBox] = []
    for coords in candidates:
        try:
            box = _validate_box(
                coords,
                min_area=min_area,
                max_area_ratio=max_area_ratio,
                normalize_order=normalize_order,
            )
        except GroundingParseError as exc:
            x1, y1, x2, y2 = coords
            invalid = ParsedBox(
                bbox=[x1, y1, x2, y2],
                center=[int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))],
                area=max(0, (x2 - x1) * (y2 - y1)),
                valid=False,
                reason=exc.code,
            )
            parsed.append(invalid)
            continue
        parsed.append(box)
        valid.append(box)
    return ParsedBoxSet(candidates=parsed, valid_candidates=valid, candidate_count=len(parsed))


def calibrate_bbox_from_resized_input(
    bbox: list[int], *, original_size: tuple[int, int], resized_size: tuple[int, int]
) -> list[int]:
    """Return normalized bbox after validating resize metadata.

    LocateAnything emits normalized 0-1000 coordinates, so aspect-preserving
    resize usually needs no calibration. This helper exists to make that
    contract explicit and testable, while rejecting impossible metadata.
    """

    if len(bbox) != 4:
        raise GroundingParseError("invalid_bbox", "bbox must contain four coordinates")
    if min(original_size + resized_size) <= 0:
        raise GroundingParseError("invalid_resize", "image sizes must be positive")
    if any(value < 0 or value > 1000 for value in bbox):
        raise GroundingParseError("out_of_range", "bbox coordinates must be in 0-1000")
    return [int(value) for value in bbox]
