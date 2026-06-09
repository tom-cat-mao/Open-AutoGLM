"""Summary reporting for grounding benchmark predictions."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from bench.grounding.scoring import bbox_iou, center_in_box, score_grounding_case, valid_bbox


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def primary_bbox(case: dict[str, Any]) -> list[int] | None:
    for element in case.get("elements", []):
        bbox = element.get("bbox")
        if element.get("required", True) and valid_bbox(bbox):
            return [int(round(v)) for v in bbox]
    for element in case.get("elements", []):
        bbox = element.get("bbox")
        if valid_bbox(bbox):
            return [int(round(v)) for v in bbox]
    return None


def center_distance_0_1000(pred: list[int] | None, gold: list[int] | None) -> float | None:
    if pred is None or gold is None:
        return None
    px = (pred[0] + pred[2]) / 2
    py = (pred[1] + pred[3]) / 2
    gx = (gold[0] + gold[2]) / 2
    gy = (gold[1] + gold[3]) / 2
    return math.sqrt((px - gx) ** 2 + (py - gy) ** 2)


def enrich_prediction(prediction: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    score = score_grounding_case(prediction, case)
    pred_bbox = prediction.get("bbox")
    if pred_bbox is None:
        boxes = prediction.get("boxes") or []
        pred_bbox = boxes[0] if boxes else None
    pred_bbox = [int(round(v)) for v in pred_bbox] if valid_bbox(pred_bbox) else None
    gold_bbox = primary_bbox(case)
    iou = bbox_iou(pred_bbox, gold_bbox) if pred_bbox and gold_bbox else 0.0
    center_hit = center_in_box(pred_bbox, gold_bbox) if pred_bbox and gold_bbox else False

    metadata = case.get("metadata") or {}
    return {
        **prediction,
        "gold_bbox": gold_bbox,
        "target_type": metadata.get("target_type"),
        "area_bucket": metadata.get("area_bucket"),
        "score": score.to_dict(),
        "iou": round(iou, 6),
        "center_hit": center_hit,
        "center_distance": center_distance_0_1000(pred_bbox, gold_bbox),
    }


def summarize_predictions(items: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(items)
    if not count:
        return {"count": 0}
    latencies = [item["latency_ms"] for item in items if isinstance(item.get("latency_ms"), (int, float))]
    distances = [item["center_distance"] for item in items if isinstance(item.get("center_distance"), (int, float))]
    scores = [item.get("score", {}) for item in items]
    return {
        "count": count,
        "parse_success_rate": _rate(items, "parsed"),
        "success_rate": _rate(items, "success"),
        "center_hit_rate": _rate(items, "center_hit"),
        "acc_iou_0_3": sum(float(item.get("iou") or 0.0) >= 0.3 for item in items) / count,
        "acc_iou_0_5": sum(float(item.get("iou") or 0.0) >= 0.5 for item in items) / count,
        "mean_iou": statistics.mean(float(item.get("iou") or 0.0) for item in items),
        "required_recall": statistics.mean(float(score.get("required_recall") or 0.0) for score in scores),
        "precision": statistics.mean(float(score.get("precision") or 0.0) for score in scores),
        "mean_center_distance_0_1000": statistics.mean(distances) if distances else None,
        "latency_ms_mean": statistics.mean(latencies) if latencies else None,
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": percentile(latencies, 0.95) if latencies else None,
    }


def grouped_summary(items: list[dict[str, Any]], key: str, max_groups: int = 30) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item.get(key) or "unknown"), []).append(item)
    ordered = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:max_groups]
    return {name: summarize_predictions(rows) for name, rows in ordered}


def build_summary(items: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    parse_errors: dict[str, int] = {}
    for item in items:
        code = item.get("parse_error") or item.get("runner_error") or item.get("failure_code")
        if code:
            parse_errors[str(code)] = parse_errors.get(str(code), 0) + 1
    return {
        "metadata": metadata or {},
        "overall": summarize_predictions(items),
        "by_target_type": grouped_summary(items, "target_type"),
        "by_area_bucket": grouped_summary(items, "area_bucket"),
        "parse_errors": parse_errors,
    }


def percentile(values: list[int | float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return float(ordered[index])


def _rate(items: list[dict[str, Any]], key: str) -> float:
    return sum(bool(item.get(key)) for item in items) / len(items) if items else 0.0
