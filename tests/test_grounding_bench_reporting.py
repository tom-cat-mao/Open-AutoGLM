import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.grounding.reporting import (  # noqa: E402
    build_summary,
    enrich_prediction,
    percentile,
)


def test_enrich_prediction_adds_iou_center_and_score() -> None:
    case = {
        "id": "case_1",
        "image": "/tmp/screen.png",
        "prompt": "tap Wi-Fi",
        "elements": [{"id": "target", "bbox": [100, 100, 300, 300], "required": True}],
        "metadata": {"target_type": "android.widget.TextView", "area_bucket": "medium"},
    }
    prediction = {
        "case_id": "case_1",
        "bbox": [110, 110, 290, 290],
        "success": True,
        "parsed": True,
        "latency_ms": 123,
    }

    enriched = enrich_prediction(prediction, case)

    assert enriched["center_hit"] is True
    assert enriched["iou"] > 0.8
    assert enriched["target_type"] == "android.widget.TextView"
    assert enriched["score"]["required_recall"] == 1.0


def test_build_summary_groups_metrics() -> None:
    items = [
        {
            "case_id": "a",
            "parsed": True,
            "success": True,
            "center_hit": True,
            "iou": 0.6,
            "latency_ms": 10,
            "center_distance": 0.0,
            "target_type": "A",
            "area_bucket": "small",
            "score": {"required_recall": 1.0, "precision": 1.0},
        },
        {
            "case_id": "b",
            "parsed": False,
            "success": False,
            "center_hit": False,
            "iou": 0.0,
            "parse_error": "invalid_format",
            "target_type": "B",
            "area_bucket": "medium",
            "score": {"required_recall": 0.0, "precision": 0.0},
        },
    ]

    summary = build_summary(items)

    assert summary["overall"]["count"] == 2
    assert summary["overall"]["center_hit_rate"] == 0.5
    assert summary["overall"]["acc_iou_0_5"] == 0.5
    assert summary["parse_errors"] == {"invalid_format": 1}
    assert set(summary["by_target_type"]) == {"A", "B"}


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5.0
