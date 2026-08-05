"""CLI runner for benchmarking LocateAnything on grounding manifests."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from bench.grounding.datasets import (
    DatasetFilters,
    SamplingConfig,
    build_post_training_manifest,
    read_manifest,
    write_manifest,
)
from bench.grounding.reporting import build_summary, enrich_prediction, write_jsonl
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.parser import GroundingParseError
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, ScreenBinding


@dataclass(frozen=True)
class FileScreenshot:
    base64_data: str


def load_screenshot(path: Path) -> tuple[FileScreenshot, ScreenBinding]:
    raw = path.read_bytes()
    with Image.open(path) as image:
        width, height = image.size
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return (
        FileScreenshot(base64_data=base64.b64encode(raw).decode("ascii")),
        ScreenBinding(
            screen_id=digest,
            raw_screenshot_hash=digest,
            width=width,
            height=height,
        ),
    )


def _valid_scope(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        scope = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if any(not (0.0 <= v <= 1000.0) for v in scope):
        return None
    if scope[2] <= scope[0] or scope[3] <= scope[1]:
        return None
    return scope


def _scope_crop(
    image: Image.Image, scope: Sequence[float]
) -> tuple[Image.Image, tuple[float, float], tuple[float, float]]:
    """Crop a 0-1000 scope region out of the full frame (no padding, exact
    region) and return the crop plus its origin/size in 0-1000 space so the
    provider box can be affinely mapped back to full-frame coordinates."""

    width, height = image.size
    sx1, sy1, sx2, sy2 = (float(v) for v in scope)
    px1, py1 = sx1 * width / 1000.0, sy1 * height / 1000.0
    px2, py2 = sx2 * width / 1000.0, sy2 * height / 1000.0
    cx1 = int(max(0.0, min(px1, width - 1)))
    cy1 = int(max(0.0, min(py1, height - 1)))
    cx2 = int(min(width, max(px2, cx1 + 1)))
    cy2 = int(min(height, max(py2, cy1 + 1)))
    if cx2 <= cx1 or cy2 <= cy1:
        raise ValueError("degenerate scope region")
    crop = image.crop((cx1, cy1, cx2, cy2))
    origin = (cx1 / width * 1000.0, cy1 / height * 1000.0)
    size = ((cx2 - cx1) / width * 1000.0, (cy2 - cy1) / height * 1000.0)
    return crop, origin, size


def _map_box_to_full(
    box: Sequence[float], origin: tuple[float, float], size: tuple[float, float]
) -> list[int]:
    ox, oy = origin
    sx, sy = size
    bx1, by1, bx2, by2 = (float(v) for v in box)
    return [
        int(round(ox + bx1 * sx / 1000.0)),
        int(round(oy + by1 * sy / 1000.0)),
        int(round(ox + bx2 * sx / 1000.0)),
        int(round(oy + by2 * sy / 1000.0)),
    ]


def _map_point_to_full(
    point: Sequence[float], origin: tuple[float, float], size: tuple[float, float]
) -> list[int]:
    ox, oy = origin
    sx, sy = size
    return [
        int(round(ox + float(point[0]) * sx / 1000.0)),
        int(round(oy + float(point[1]) * sy / 1000.0)),
    ]


def _remap_candidate(
    candidate: Any, origin: tuple[float, float], size: tuple[float, float]
) -> Any:
    if not isinstance(candidate, MarkCandidate):
        return candidate
    return dataclasses.replace(
        candidate,
        bbox=_map_box_to_full(candidate.bbox, origin, size),
        center=_map_point_to_full(candidate.center, origin, size),
    )


def run_case(provider: LocateAnythingMLXProvider, case: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    image_path = Path(str(case.get("image") or ""))
    prompt = str(case.get("prompt") or "").strip()
    prediction: dict[str, Any] = {
        "case_id": str(case.get("id")),
        "image": str(image_path),
        "prompt": prompt,
        "provider": provider.name,
    }
    if not image_path.is_file():
        return {
            **prediction,
            "success": False,
            "parsed": False,
            "runner_error": "missing_image",
            "latency_ms": None,
        }
    if not prompt:
        return {
            **prediction,
            "success": False,
            "parsed": False,
            "runner_error": "missing_prompt",
            "latency_ms": None,
        }

    try:
        screenshot, binding = load_screenshot(image_path)
        # Optional per-case scope (0-1000 region): query the provider against
        # the high-resolution crop and back-map its box to full-frame
        # coordinates, mirroring the runtime scoped-locate path (S2).
        scope = _valid_scope(case.get("scope"))
        scope_origin: tuple[float, float] | None = None
        scope_size: tuple[float, float] | None = None
        if scope is not None:
            with Image.open(image_path) as full:
                crop, scope_origin, scope_size = _scope_crop(full.convert("RGB"), scope)
            buffered = io.BytesIO()
            crop.save(buffered, format="PNG")
            screenshot = FileScreenshot(base64_data=base64.b64encode(buffered.getvalue()).decode("ascii"))
        result = provider.provide_marks(screenshot, binding, hints=[MarkProviderHint(text=prompt)], timeout=timeout)
        if result.success and scope_origin is not None and scope_size is not None:
            result = dataclasses.replace(
                result,
                marks=[_remap_candidate(mark, scope_origin, scope_size) for mark in result.marks],
                candidates=[_remap_candidate(candidate, scope_origin, scope_size) for candidate in result.candidates],
            )
    except GroundingParseError as exc:
        return {
            **prediction,
            "success": False,
            "parsed": False,
            "parse_error": exc.code,
            "latency_ms": None,
        }
    except Exception as exc:
        return {
            **prediction,
            "success": False,
            "parsed": False,
            "runner_error": f"{type(exc).__name__}: {exc}",
            "latency_ms": None,
        }

    output = {
        **prediction,
        "success": result.success,
        "parsed": result.success,
        "bbox": result.marks[0].bbox if result.marks else None,
        "boxes": [mark.bbox for mark in result.marks],
        "latency_ms": result.latency_ms,
        "candidate_count": result.candidate_count,
        "failure_code": result.failure_code,
        "parse_error": None if result.success else result.failure_code,
        "provider_input_hash": result.provider_input_hash,
        "selected_candidate_id": 0 if result.marks else None,
    }
    return {key: value for key, value in output.items() if value is not None}


def build_cases_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.manifest:
        cases = read_manifest(Path(args.manifest))
        return cases[: args.limit] if args.limit else cases

    if not args.post_training_data:
        raise SystemExit("provide --manifest or --post-training-data")

    filters = DatasetFilters(
        clean=args.clean,
        min_area_ratio=args.min_area_ratio,
        exclude_weak_types=args.exclude_weak_types,
        trusted_types_only=args.trusted_types_only,
        unique_images=not args.allow_duplicate_images,
    )
    sampling = SamplingConfig(
        limit=args.limit,
        seed=args.seed,
        sampling=args.sampling,
        per_type_cap=args.per_type_cap,
        per_area_cap=args.per_area_cap,
    )
    cases = build_post_training_manifest(Path(args.post_training_data), filters=filters, sampling=sampling)
    if args.manifest_output:
        write_manifest(
            Path(args.manifest_output),
            cases,
            metadata={
                "source": str(args.post_training_data),
                "filters": filters.__dict__,
                "sampling": sampling.__dict__,
            },
        )
    return cases


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="LocateAnything model path")
    parser.add_argument("--output", required=True, help="Prediction JSONL output path")
    parser.add_argument("--summary-output", required=True, help="Summary JSON output path")
    parser.add_argument("--manifest", help="Benchmark manifest JSON")
    parser.add_argument("--post-training-data", help="post-training raw.jsonl dataset")
    parser.add_argument("--manifest-output", help="Optional sampled manifest output path")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sampling", choices=["random", "balanced"], default="random")
    parser.add_argument("--per-type-cap", type=int, default=30)
    parser.add_argument("--per-area-cap", type=int, default=120)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--min-area-ratio", type=float, default=0.0005)
    parser.add_argument("--exclude-weak-types", action="store_true")
    parser.add_argument("--trusted-types-only", action="store_true")
    parser.add_argument("--allow-duplicate-images", action="store_true")
    parser.add_argument("--max-size", type=int, default=960)
    # R1/R3: run the provider at original resolution (no thumbnail). Phone
    # screenshots are far below the 2048 locate-tier box, so this is the
    # highest-fidelity bench tier; pass --max-size 2048 for an explicit tier.
    parser.add_argument("--skip-thumbnail", action="store_true", help="disable input downscaling (original resolution)")
    parser.add_argument("--timeout", type=float)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cases = build_cases_from_args(args)
    if not cases:
        raise SystemExit("no benchmark cases selected")

    provider = LocateAnythingMLXProvider(
        args.model,
        max_size=None if args.skip_thumbnail else args.max_size,
    )
    print(f"Running LocateAnything on {len(cases)} cases max_size={args.max_size}", flush=True)
    predictions: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        prediction = enrich_prediction(run_case(provider, case, timeout=args.timeout), case)
        predictions.append(prediction)
        print(
            f"[{index}/{len(cases)}] ok={prediction.get('success')} hit={prediction.get('center_hit')} "
            f"iou={float(prediction.get('iou') or 0.0):.3f} lat={prediction.get('latency_ms')}ms "
            f"type={prediction.get('target_type')} prompt={str(case.get('prompt') or '')[:48]!r}",
            flush=True,
        )

    output_path = Path(args.output)
    write_jsonl(output_path, predictions)
    summary = build_summary(
        predictions,
        metadata={
            "model": args.model,
            "max_size": args.max_size,
            "limit": args.limit,
            "seed": args.seed,
            "sampling": args.sampling,
            "output": str(output_path),
        },
    )
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
