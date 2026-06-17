import importlib.util
import sys
from pathlib import Path

SCORING_PATH = Path(__file__).resolve().parents[1] / "bench" / "grounding" / "scoring.py"
SPEC = importlib.util.spec_from_file_location("grounding_bench_scoring", SCORING_PATH)
assert SPEC is not None and SPEC.loader is not None
grounding_bench_scoring = importlib.util.module_from_spec(SPEC)
sys.modules["grounding_bench_scoring"] = grounding_bench_scoring
SPEC.loader.exec_module(grounding_bench_scoring)

bbox_iou = grounding_bench_scoring.bbox_iou
score_grounding_case = grounding_bench_scoring.score_grounding_case
text_f1 = grounding_bench_scoring.text_f1


def test_bbox_iou_exact_match() -> None:
    assert bbox_iou([0, 0, 100, 100], [0, 0, 100, 100]) == 1.0


def test_score_requires_all_required_elements() -> None:
    case = {
        "id": "multi",
        "elements": [
            {"id": "row", "bbox": [100, 100, 500, 200], "required": True},
            {"id": "button", "bbox": [520, 100, 620, 200], "required": True},
        ],
    }

    score = score_grounding_case({"boxes": [[100, 100, 500, 200]]}, case)

    assert score.required_recall == 0.5
    assert score.failure_code == "missing_required_element"


def test_score_accepts_small_clickable_center_hit_with_low_iou() -> None:
    case = {
        "id": "small_icon",
        "elements": [{"id": "icon", "bbox": [500, 500, 540, 540], "required": True}],
    }

    score = score_grounding_case({"bbox": [495, 495, 545, 545]}, case)

    assert score.required_recall == 1.0
    assert score.click_accuracy == 1.0


def test_text_f1_normalizes_case_and_punctuation() -> None:
    assert text_f1("Wi-Fi!", "wifi") == 1.0


def test_score_combines_text_and_location() -> None:
    case = {
        "id": "text_case",
        "elements": [{"id": "wifi", "bbox": [40, 100, 960, 200], "required": True}],
        "expected_text": {"target_label": "Wi-Fi"},
    }

    score = score_grounding_case(
        {"boxes": [[42, 102, 958, 198]], "text": {"target_label": "Wi-Fi"}},
        case,
    )

    assert score.required_recall == 1.0
    assert score.text_score == 1.0
    assert score.score > 0.9
