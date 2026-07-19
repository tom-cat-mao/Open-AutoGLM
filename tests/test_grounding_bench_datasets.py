import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.grounding.datasets import (  # noqa: E402
    DatasetFilters,
    SamplingConfig,
    area_bucket,
    bbox_0_1_to_1000,
    build_post_training_manifest,
    post_training_rows_to_cases,
    sample_cases,
)


def test_bbox_0_1_to_1000_clamps_and_rounds() -> None:
    assert bbox_0_1_to_1000([-0.1, 0.1245, 0.9996, 1.4]) == [0, 124, 1000, 1000]


def test_post_training_rows_to_cases_converts_schema(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"placeholder")
    rows = [
        {
            "image_path": str(image),
            "instruction": "tap Wi-Fi",
            "bbox": [0.1, 0.2, 0.4, 0.5],
            "target_type": "android.widget.TextView",
            "source": "unit",
        }
    ]

    cases = post_training_rows_to_cases(rows, DatasetFilters())

    assert len(cases) == 1
    assert cases[0]["image"] == str(image)
    assert cases[0]["prompt"] == "tap Wi-Fi"
    assert cases[0]["elements"][0]["bbox"] == [100, 200, 400, 500]
    assert cases[0]["metadata"]["target_type"] == "android.widget.TextView"


def test_clean_filters_remove_weak_and_tiny_records(tmp_path: Path) -> None:
    image_a = tmp_path / "a.png"
    image_b = tmp_path / "b.png"
    image_a.write_bytes(b"a")
    image_b.write_bytes(b"b")
    rows = [
        {
            "image_path": str(image_a),
            "instruction": "weak",
            "bbox": [0.1, 0.1, 0.5, 0.5],
            "target_type": "android.view.View",
        },
        {
            "image_path": str(image_b),
            "instruction": "tiny",
            "bbox": [0.1, 0.1, 0.101, 0.101],
            "target_type": "android.widget.TextView",
        },
    ]

    cases = post_training_rows_to_cases(
        rows,
        DatasetFilters(clean=True, exclude_weak_types=True, min_area_ratio=0.0005),
    )

    assert cases == []


def test_balanced_sampling_respects_caps_then_fills() -> None:
    cases = [
        {"id": "a1", "metadata": {"target_type": "A", "area_bucket": "small"}},
        {"id": "a2", "metadata": {"target_type": "A", "area_bucket": "small"}},
        {"id": "b1", "metadata": {"target_type": "B", "area_bucket": "medium"}},
    ]

    sampled = sample_cases(cases, SamplingConfig(limit=3, seed=1, sampling="balanced", per_type_cap=1, per_area_cap=2))

    assert len(sampled) == 3
    assert {case["id"] for case in sampled} == {"a1", "a2", "b1"}


def test_build_post_training_manifest_from_jsonl(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"placeholder")
    data = tmp_path / "raw.jsonl"
    data.write_text(
        json.dumps(
            {
                "image_path": str(image),
                "instruction": "tap OK",
                "bbox": [0.2, 0.2, 0.4, 0.4],
                "target_type": "android.widget.Button",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cases = build_post_training_manifest(data, filters=DatasetFilters(), sampling=SamplingConfig(limit=1))

    assert len(cases) == 1
    assert cases[0]["elements"][0]["bbox"] == [200, 200, 400, 400]


def test_area_bucket_boundaries() -> None:
    assert area_bucket([0, 0, 10, 10]) == "tiny"
    assert area_bucket([0, 0, 40, 40]) == "small"
    assert area_bucket([0, 0, 100, 100]) == "medium"
    assert area_bucket([0, 0, 500, 500]) == "large"
