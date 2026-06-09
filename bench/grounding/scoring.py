"""Scoring primitives for GUI/visual grounding benchmark cases.

The runtime grounding contract uses normalized 0-1000 boxes.  This module keeps
the benchmark independent from MLX/ADB so dataset predictions can be scored in
CI and offline notebooks.
"""

from __future__ import annotations

import re
import string
from dataclasses import asdict, dataclass
from typing import Any

BBox = list[int]


@dataclass(frozen=True)
class BoxMatch:
    """One matched prediction-to-ground-truth pair."""

    pred_index: int
    gt_index: int
    gt_id: str
    iou: float
    center_hit: bool


@dataclass(frozen=True)
class GroundingCaseScore:
    """Stable JSON-serializable score for one benchmark case."""

    case_id: str
    score: float
    localization_score: float
    required_recall: float
    optional_recall: float
    precision: float
    text_score: float
    matched_required: int
    total_required: int
    matched_optional: int
    total_optional: int
    false_positive_count: int
    mean_iou: float
    click_accuracy: float
    matches: list[BoxMatch]
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: Any) -> str:
    """Normalize text for OCR/UI-label answer comparison."""

    text = "" if value is None else str(value)
    text = text.casefold().strip()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text)


def text_f1(prediction: Any, reference: Any) -> float:
    """Token-level F1 with exact-match fast path."""

    pred = normalize_text(prediction)
    ref = normalize_text(reference)
    if not pred and not ref:
        return 1.0
    if pred == ref:
        return 1.0
    pred_tokens = pred.split()
    ref_tokens = ref.split()
    if not pred_tokens or not ref_tokens:
        return 0.0
    overlap = 0
    remaining = ref_tokens.copy()
    for token in pred_tokens:
        if token in remaining:
            overlap += 1
            remaining.remove(token)
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_tokens)
    recall = overlap / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)


def valid_bbox(bbox: Any) -> bool:
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) for v in bbox)
        and all(0 <= float(v) <= 1000 for v in bbox)
        and float(bbox[2]) > float(bbox[0])
        and float(bbox[3]) > float(bbox[1])
    )


def bbox_iou(a: BBox, b: BBox) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return 0.0 if union <= 0 else inter / union


def center_in_box(pred: BBox, gt: BBox, *, margin: int = 0) -> bool:
    cx = (pred[0] + pred[2]) / 2
    cy = (pred[1] + pred[3]) / 2
    return gt[0] - margin <= cx <= gt[2] + margin and gt[1] - margin <= cy <= gt[3] + margin


def greedy_match_boxes(
    predictions: list[BBox],
    gt_elements: list[dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
    small_element_iou_threshold: float = 0.1,
    center_margin: int = 5,
) -> list[BoxMatch]:
    """Greedy one-to-one matching.

    Primary criterion is IoU>=0.5.  For tiny UI controls, click usefulness is
    better represented by the predicted center falling inside the GT box, so a
    low-IoU center-hit fallback is also accepted.
    """

    candidates: list[tuple[float, int, int, bool]] = []
    for pred_idx, pred in enumerate(predictions):
        if not valid_bbox(pred):
            continue
        clean_pred = [int(round(v)) for v in pred]
        for gt_idx, gt in enumerate(gt_elements):
            gt_box = gt.get("bbox")
            if not valid_bbox(gt_box):
                continue
            clean_gt = [int(round(v)) for v in gt_box]
            iou = bbox_iou(clean_pred, clean_gt)
            hit = center_in_box(clean_pred, clean_gt, margin=center_margin)
            accepted = iou >= iou_threshold or (hit and iou >= small_element_iou_threshold)
            if accepted:
                # Prefer high IoU; center-hit breaks ties for clickable accuracy.
                candidates.append((iou + (0.001 if hit else 0.0), pred_idx, gt_idx, hit))

    matches: list[BoxMatch] = []
    used_preds: set[int] = set()
    used_gts: set[int] = set()
    for score, pred_idx, gt_idx, hit in sorted(candidates, reverse=True):
        if pred_idx in used_preds or gt_idx in used_gts:
            continue
        used_preds.add(pred_idx)
        used_gts.add(gt_idx)
        matches.append(
            BoxMatch(
                pred_index=pred_idx,
                gt_index=gt_idx,
                gt_id=str(gt_elements[gt_idx].get("id", gt_idx)),
                iou=max(0.0, min(1.0, score - (0.001 if hit else 0.0))),
                center_hit=hit,
            )
        )
    return matches


def _collect_pred_boxes(prediction: dict[str, Any]) -> list[BBox]:
    raw_boxes = prediction.get("boxes")
    if raw_boxes is None and prediction.get("bbox") is not None:
        raw_boxes = [prediction.get("bbox")]
    if raw_boxes is None:
        raw_boxes = []
    boxes: list[BBox] = []
    for box in raw_boxes:
        if valid_bbox(box):
            boxes.append([int(round(v)) for v in box])
    return boxes


def _score_text(prediction: dict[str, Any], case: dict[str, Any]) -> float:
    expected = case.get("expected_text") or {}
    if not expected:
        return 1.0
    pred_text = prediction.get("text") or {}
    if isinstance(pred_text, str):
        pred_text = {"answer": pred_text}
    scores = []
    for key, ref in expected.items():
        scores.append(text_f1(pred_text.get(key), ref))
    return sum(scores) / len(scores) if scores else 1.0


def score_grounding_case(
    prediction: dict[str, Any],
    case: dict[str, Any],
    *,
    iou_threshold: float = 0.5,
    weights: dict[str, float] | None = None,
) -> GroundingCaseScore:
    """Score one prediction against one manifest case.

    Expected case shape:
      {"id": str, "elements": [{"id": str, "bbox": [x1,y1,x2,y2], "required": bool}],
       "expected_text": {"field": "reference"}}

    Prediction shape:
      {"bbox": [...]} or {"boxes": [[...]], "text": {"field": "answer"}}
    """

    weights = weights or {"localization": 0.55, "coverage": 0.25, "text": 0.20}
    case_id = str(case.get("id", "unknown"))
    elements = [e for e in case.get("elements", []) if valid_bbox(e.get("bbox"))]
    required = [e for e in elements if e.get("required", True)]
    optional = [e for e in elements if not e.get("required", True)]
    pred_boxes = _collect_pred_boxes(prediction)

    if not elements:
        return GroundingCaseScore(
            case_id=case_id,
            score=0.0,
            localization_score=0.0,
            required_recall=0.0,
            optional_recall=0.0,
            precision=0.0,
            text_score=0.0,
            matched_required=0,
            total_required=0,
            matched_optional=0,
            total_optional=0,
            false_positive_count=len(pred_boxes),
            mean_iou=0.0,
            click_accuracy=0.0,
            matches=[],
            failure_code="missing_ground_truth",
        )

    matches = greedy_match_boxes(pred_boxes, elements, iou_threshold=iou_threshold)
    matched_gt_ids = {m.gt_id for m in matches}
    matched_required = sum(1 for e in required if str(e.get("id")) in matched_gt_ids)
    matched_optional = sum(1 for e in optional if str(e.get("id")) in matched_gt_ids)
    required_recall = matched_required / len(required) if required else 1.0
    optional_recall = matched_optional / len(optional) if optional else 1.0
    precision = len(matches) / len(pred_boxes) if pred_boxes else 0.0
    false_positive_count = max(0, len(pred_boxes) - len(matches))
    mean_iou = sum(m.iou for m in matches) / len(matches) if matches else 0.0
    click_accuracy = sum(1 for m in matches if m.center_hit) / len(elements) if elements else 0.0
    localization_score = (0.7 * mean_iou) + (0.3 * click_accuracy)
    coverage_score = (0.8 * required_recall) + (0.2 * optional_recall)
    text_score = _score_text(prediction, case)
    final_score = (
        weights["localization"] * localization_score
        + weights["coverage"] * coverage_score
        + weights["text"] * text_score
    )

    failure_code = None
    if required and matched_required < len(required):
        failure_code = "missing_required_element"
    elif pred_boxes and false_positive_count:
        failure_code = "has_false_positive"
    elif not pred_boxes:
        failure_code = "no_prediction"

    return GroundingCaseScore(
        case_id=case_id,
        score=round(final_score, 6),
        localization_score=round(localization_score, 6),
        required_recall=round(required_recall, 6),
        optional_recall=round(optional_recall, 6),
        precision=round(precision, 6),
        text_score=round(text_score, 6),
        matched_required=matched_required,
        total_required=len(required),
        matched_optional=matched_optional,
        total_optional=len(optional),
        false_positive_count=false_positive_count,
        mean_iou=round(mean_iou, 6),
        click_accuracy=round(click_accuracy, 6),
        matches=matches,
        failure_code=failure_code,
    )
