"""R3: the scoped-locate regression manifest is structurally valid, its image
exists at the expected resolution, and the scope crop matches the documented
1216x2066 input. The actual LocateAnything inference needs the real MLX model
(Metal) — that part is a manual run (see manifest metadata / docs), but the
manifest + scope math + ground-truth geometry are CI-verifiable here."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "bench" / "grounding" / "data"
MANIFEST = DATA / "manifest.locate-date-cell.json"
FRAME = DATA / "step_010_locate_frame.png"

SCOPE = (0, 200.38, 1000, 983.33)
EXPECTED_BBOX = (595, 713, 680, 746)
ACCEPTANCE_BOX = (571, 692, 714, 773)


def _case() -> dict:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return cases[0]


def test_manifest_exists_and_single_case() -> None:
    assert MANIFEST.is_file()
    case = _case()
    assert case["id"] == "locate_2026_10_01_date_cell"
    assert case["prompt"] == "2026年10月1日日期格"
    assert case["image"] == "bench/grounding/data/step_010_locate_frame.png"
    assert case["scope"] == list(SCOPE)
    assert case["max_size"] == 2048


def test_frame_is_the_documented_trace_capture() -> None:
    assert FRAME.is_file()
    with Image.open(FRAME) as image:
        assert image.size == (1216, 2640)


def test_scope_crop_matches_documented_1216x2066() -> None:
    with Image.open(FRAME) as image:
        width, height = image.size
    sx1, sy1, sx2, sy2 = SCOPE
    cx1 = int(max(0, min(sx1 * width / 1000.0, width - 1)))
    cy1 = int(max(0, min(sy1 * height / 1000.0, height - 1)))
    cx2 = int(min(width, max(sx2 * width / 1000.0, cx1 + 1)))
    cy2 = int(min(height, max(sy2 * height / 1000.0, cy1 + 1)))
    assert (cx2 - cx1, cy2 - cy1) == (1216, 2066)


def test_ground_truth_box_and_acceptance_are_consistent() -> None:
    """The expected box's own center must fall inside the acceptance box the
    doc specifies for the assertion (center in [571,692,714,773])."""
    x1, y1, x2, y2 = EXPECTED_BBOX
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    ax1, ay1, ax2, ay2 = ACCEPTANCE_BOX
    assert ax1 <= cx <= ax2
    assert ay1 <= cy <= ay2
    case = _case()
    assert case["elements"][0]["bbox"] == list(EXPECTED_BBOX)
    assert case["center_acceptance_box"] == list(ACCEPTANCE_BOX)


def test_scope_contains_the_expected_cell() -> None:
    """The scope band must spatially contain the ground-truth date cell."""
    x1, y1, x2, y2 = EXPECTED_BBOX
    sx1, sy1, sx2, sy2 = SCOPE
    assert sx1 <= x1 and sx2 >= x2
    assert sy1 <= y1 and sy2 >= y2
