"""Dataset adapters for grounding benchmarks.

Open-AutoGLM benchmark cases use 0-1000 normalized bboxes.  The post-training
grounding dataset stores 0-1 normalized boxes, so this module owns the
conversion, filtering, and deterministic suite sampling.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any


EXCLUDED_CLEAN_TYPES = {
    "android.webkit.WebView",
    "android.view.ViewGroup",
    "android.view.View",
    "android.widget.RelativeLayout",
}

TRUSTED_ELEMENT_TYPES = {
    "android.widget.TextView",
    "android.widget.Button",
    "android.widget.EditText",
    "android.widget.LinearLayout",
    "android.widget.ImageView",
    "android.widget.ImageButton",
    "android.widget.CheckBox",
    "android.widget.CompoundButton",
    "android.widget.RadioButton",
    "android.widget.AutoCompleteTextView",
    "android.widget.CheckedTextView",
}


@dataclass(frozen=True)
class DatasetFilters:
    clean: bool = False
    min_area_ratio: float = 0.0005
    exclude_weak_types: bool = False
    trusted_types_only: bool = False
    max_instruction_chars: int = 300
    unique_images: bool = True


@dataclass(frozen=True)
class SamplingConfig:
    limit: int
    seed: int = 42
    sampling: str = "random"
    per_type_cap: int = 30
    per_area_cap: int = 120


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        return payload["cases"]
    raise ValueError("manifest must be a list or an object with a cases list")


def write_manifest(path: Path, cases: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata or {}, "cases": cases}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bbox_0_1_to_1000(bbox: list[Any]) -> list[int]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("bbox must contain four coordinates")
    return [max(0, min(1000, int(round(float(value) * 1000)))) for value in bbox]


def bbox_area_ratio_1000(bbox: list[int]) -> float:
    width = max(0, bbox[2] - bbox[0])
    height = max(0, bbox[3] - bbox[1])
    return (width * height) / 1_000_000


def area_bucket(bbox: list[int]) -> str:
    area_ratio = bbox_area_ratio_1000(bbox)
    if area_ratio < 0.0005:
        return "tiny"
    if area_ratio < 0.005:
        return "small"
    if area_ratio < 0.05:
        return "medium"
    return "large"


def is_valid_post_training_row(row: dict[str, Any], filters: DatasetFilters) -> bool:
    image_path = str(row.get("image_path") or "")
    instruction = str(row.get("instruction") or "").strip()
    if not image_path or not Path(image_path).is_file() or not instruction:
        return False
    if len(instruction) > filters.max_instruction_chars:
        return False

    try:
        bbox = bbox_0_1_to_1000(row.get("bbox"))
    except (TypeError, ValueError):
        return False
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return False

    if not filters.clean:
        return True

    if bbox_area_ratio_1000(bbox) < filters.min_area_ratio:
        return False
    target_type = str(row.get("target_type") or "unknown")
    if filters.exclude_weak_types and target_type in EXCLUDED_CLEAN_TYPES:
        return False
    if filters.trusted_types_only and target_type not in TRUSTED_ELEMENT_TYPES:
        return False
    return True


def post_training_row_to_case(row: dict[str, Any], row_index: int) -> dict[str, Any]:
    bbox = bbox_0_1_to_1000(row["bbox"])
    instruction = str(row.get("instruction") or "").strip()
    target_type = str(row.get("target_type") or "unknown")
    image_path = str(row.get("image_path") or "")
    digest = sha1(
        json.dumps(
            {
                "image_path": image_path,
                "instruction": instruction,
                "bbox": bbox,
                "target_type": target_type,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:10]
    bucket = area_bucket(bbox)
    return {
        "id": f"post_training_{row_index:07d}_{digest}",
        "image": image_path,
        "prompt": instruction,
        "elements": [
            {
                "id": "target",
                "bbox": bbox,
                "required": True,
                "type": target_type,
            }
        ],
        "tags": ["post_training", bucket, target_type],
        "metadata": {
            "source": row.get("source"),
            "row_index": row_index,
            "target_type": target_type,
            "area_bucket": bucket,
            "area_ratio": round(bbox_area_ratio_1000(bbox), 8),
        },
    }


def post_training_rows_to_cases(rows: list[dict[str, Any]], filters: DatasetFilters) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    for row_index, row in enumerate(rows):
        if not is_valid_post_training_row(row, filters):
            continue
        image_path = str(row.get("image_path") or "")
        if filters.unique_images and image_path in seen_images:
            continue
        seen_images.add(image_path)
        cases.append(post_training_row_to_case(row, row_index))
    return cases


def sample_cases(cases: list[dict[str, Any]], config: SamplingConfig) -> list[dict[str, Any]]:
    if config.limit <= 0:
        raise ValueError("limit must be positive")
    if config.sampling not in {"random", "balanced"}:
        raise ValueError("sampling must be 'random' or 'balanced'")

    shuffled = list(cases)
    rng = random.Random(config.seed)
    rng.shuffle(shuffled)
    if config.sampling == "random":
        return shuffled[: config.limit]

    selected: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    area_counts: dict[str, int] = {}
    selected_ids: set[str] = set()
    for case in shuffled:
        metadata = case.get("metadata") or {}
        target_type = str(metadata.get("target_type") or "unknown")
        bucket = str(metadata.get("area_bucket") or "unknown")
        if type_counts.get(target_type, 0) >= config.per_type_cap:
            continue
        if area_counts.get(bucket, 0) >= config.per_area_cap:
            continue
        selected.append(case)
        selected_ids.add(str(case["id"]))
        type_counts[target_type] = type_counts.get(target_type, 0) + 1
        area_counts[bucket] = area_counts.get(bucket, 0) + 1
        if len(selected) >= config.limit:
            return selected

    for case in shuffled:
        if str(case["id"]) in selected_ids:
            continue
        selected.append(case)
        if len(selected) >= config.limit:
            break
    return selected


def build_post_training_manifest(
    data_path: Path,
    *,
    filters: DatasetFilters,
    sampling: SamplingConfig,
) -> list[dict[str, Any]]:
    rows = read_jsonl(data_path)
    cases = post_training_rows_to_cases(rows, filters)
    return sample_cases(cases, sampling)
