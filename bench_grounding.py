#!/usr/bin/env python3
"""Grounding model benchmark: LocateAnything vs LFM2.5-VL-450M vs LFM2.5-VL-1.6B.

Captures ADB screenshots, tests at multiple resize scales, and produces
a comparison table of latency, bbox quality, and success rate.

All models run via MLX directly (no LM Studio API).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from phone_agent.grounding.parser import GroundingParseError, parse_box_response

# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------


def adb_screencap(device_id: str | None = None) -> bytes:
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["exec-out", "screencap", "-p"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def adb_screen_size(device_id: str | None = None) -> tuple[int, int]:
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "wm", "size"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    m = re.search(r"(\d+)x(\d+)", result.stdout)
    if not m:
        raise RuntimeError(f"Cannot parse screen size: {result.stdout}")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def resize_image(image: Image.Image, scale: float) -> Image.Image:
    if scale >= 1.0:
        return image.copy()
    w, h = image.size
    return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def image_hash(image: Image.Image) -> str:
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return hashlib.sha256(buf.getvalue()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Targets — Settings app elements on Android
# ---------------------------------------------------------------------------

TARGETS: list[dict[str, Any]] = [
    {"id": "wifi", "desc": "Wi-Fi", "hint": "Wi-Fi or WLAN settings item"},
    {"id": "bluetooth", "desc": "Bluetooth", "hint": "Bluetooth settings item"},
    {"id": "display", "desc": "Display", "hint": "Display or screen settings item"},
    {"id": "battery", "desc": "Battery", "hint": "Battery settings item"},
    {"id": "storage", "desc": "Storage", "hint": "Storage settings item"},
    {"id": "apps", "desc": "Apps", "hint": "Apps or Applications settings item"},
    {"id": "search", "desc": "Search icon", "hint": "Search magnifying glass icon at top"},
    {"id": "back", "desc": "Back arrow", "hint": "Back navigation arrow at top left"},
]

SCALES = [1.0, 0.5, 0.25, 0.125]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SingleResult:
    model: str
    target_id: str
    scale: float
    image_size: tuple[int, int]
    success: bool
    bbox: list[int] | None = None
    center: list[int] | None = None
    raw_output: str = ""
    error: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ImageVariant:
    """One offline preprocessing strategy for LocateAnything benchmark."""

    name: str
    kind: str
    max_size: int | None = None
    splits: int | None = None
    overlap: float = 0.0


@dataclass
class VariantResult:
    target_id: str
    variant: str
    success: bool
    bbox: list[int] | None
    center: list[int] | None
    latency_ms: float
    calls: int
    input_sizes: list[tuple[int, int]]
    reference_bbox: list[int] | None = None
    iou_vs_reference: float | None = None
    center_distance_vs_reference: float | None = None
    error: str = ""
    raw_output: str = ""


@dataclass
class BenchmarkReport:
    screen_size: tuple[int, int] = (0, 0)
    results: list[SingleResult] = field(default_factory=list)

    def summary_table(self) -> str:
        lines = []
        sep = "-" * 130
        header = (
            f"{'Model':<30} {'Target':<12} {'Scale':>6} {'ImgSize':>12} "
            f"{'OK':>4} {'Lat(ms)':>9} {'BBox (0-1000)':>28} {'Error'}"
        )
        lines.append(sep)
        lines.append(header)
        lines.append(sep)
        for r in self.results:
            bbox_str = str(r.bbox) if r.bbox else "-"
            err = r.error[:50] if r.error else ""
            lines.append(
                f"{r.model:<30} {r.target_id:<12} {r.scale:>5.0%} "
                f"{str(r.image_size):>12} {'Y' if r.success else 'N':>4} "
                f"{r.latency_ms:>8.0f}ms {bbox_str:<28} {err}"
            )
        lines.append(sep)
        return "\n".join(lines)

    def aggregate_table(self) -> str:
        lines = []
        sep = "-" * 110
        header = (
            f"{'Model':<30} {'Total':>6} {'OK':>6} {'Fail':>6} "
            f"{'Rate':>7} {'AvgLat':>8} {'MinLat':>8} {'MaxLat':>8}"
        )
        lines.append(sep)
        lines.append(header)
        lines.append(sep)

        models = sorted(set(r.model for r in self.results))
        for model in models:
            mr = [r for r in self.results if r.model == model]
            total = len(mr)
            ok = sum(1 for r in mr if r.success)
            fail = total - ok
            rate = f"{ok / total:.0%}" if total else "N/A"
            lats = [r.latency_ms for r in mr if r.success]
            avg_lat = sum(lats) / len(lats) if lats else 0
            min_lat = min(lats) if lats else 0
            max_lat = max(lats) if lats else 0
            lines.append(
                f"{model:<30} {total:>6} {ok:>6} {fail:>6} "
                f"{rate:>7} {avg_lat:>7.0f}ms {min_lat:>7.0f}ms {max_lat:>7.0f}ms"
            )
        lines.append(sep)

        # Per-scale breakdown
        lines.append("")
        lines.append("Per-scale success rate (OK/total):")
        lines.append("-" * 90)
        scale_header = f"{'Model':<30} " + "  ".join(f"{s:.0%}" for s in SCALES)
        lines.append(scale_header)
        lines.append("-" * 90)
        for model in models:
            rates = []
            for scale in SCALES:
                scaled = [r for r in self.results if r.model == model and r.scale == scale]
                ok = sum(1 for r in scaled if r.success)
                rates.append(f"{ok}/{len(scaled)}")
            lines.append(f"{model:<30} " + "    ".join(rates))
        lines.append("-" * 90)

        # Per-scale avg latency
        lines.append("")
        lines.append("Per-scale avg latency (ms):")
        lines.append("-" * 90)
        lines.append(scale_header)
        lines.append("-" * 90)
        for model in models:
            lats = []
            for scale in SCALES:
                scaled = [r for r in self.results if r.model == model and r.scale == scale and r.success]
                avg = sum(r.latency_ms for r in scaled) / len(scaled) if scaled else 0
                lats.append(f"{avg:.0f}")
            lines.append(f"{model:<30} " + "    ".join(lats))
        lines.append("-" * 90)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model runners
# ---------------------------------------------------------------------------


class LocateAnythingRunner:
    """LocateAnything-3B-4bit via MLX with PBD."""

    name = "LocateAnything-3B-4bit"

    def __init__(self, model_path: str, max_size: int = 1280):
        self.model_path = Path(model_path)
        self.max_size = max_size
        self._model = None
        self._processor = None

    def _load(self):
        if self._model is not None:
            return
        from mlx_vlm import load  # type: ignore

        print(f"  [Loading {self.name} from {self.model_path}...]", flush=True)
        self._model, self._processor = load(str(self.model_path))

    def run(self, image: Image.Image, target: dict[str, Any]) -> SingleResult:
        self._load()

        # Resize to max_size to avoid Metal OOM
        image = image.copy()
        image.thumbnail((self.max_size, self.max_size))

        return self.run_prepared(image, target)

    def run_prepared(self, image: Image.Image, target: dict[str, Any]) -> SingleResult:
        """Run LocateAnything on an already-preprocessed image."""
        self._load()
        from mlx_vlm import generate  # type: ignore
        from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore

        description = target["hint"]
        prompt = f"Locate the region that matches the following description: {description}."

        started = time.perf_counter()
        try:
            pbd_generate = getattr(self._model, "pbd_generate", None)
            if callable(pbd_generate):
                output = self._run_pbd(image, prompt)
            else:
                chat_prompt = apply_chat_template(
                    self._processor, self._model.config, prompt, num_images=1
                )
                result = generate(
                    self._model, self._processor,
                    prompt=chat_prompt, image=image,
                    max_tokens=2048, temperature=0.0,
                    generation_mode="hybrid",
                )
                output = str(getattr(result, "text", result))
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            return SingleResult(
                model=self.name, target_id=target["id"], scale=1.0,
                image_size=image.size, success=False,
                error=f"{type(exc).__name__}: {exc}", latency_ms=latency,
            )

        latency = (time.perf_counter() - started) * 1000
        bbox = self._parse_box(output)
        if bbox is None:
            return SingleResult(
                model=self.name, target_id=target["id"], scale=1.0,
                image_size=image.size, success=False,
                raw_output=output[:200],
                error=f"parse_failed: {output[:100]}", latency_ms=latency,
            )
        center = [
            int(round((bbox[0] + bbox[2]) / 2)),
            int(round((bbox[1] + bbox[3]) / 2)),
        ]
        return SingleResult(
            model=self.name, target_id=target["id"], scale=1.0,
            image_size=image.size, success=True,
            bbox=bbox, center=center, raw_output=output[:200], latency_ms=latency,
        )

    def _run_pbd(self, image: Image.Image, prompt: str) -> str:
        from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore
        from mlx_vlm.utils import prepare_inputs  # type: ignore

        chat_prompt = apply_chat_template(
            self._processor, self._model.config, prompt, num_images=1
        )
        inputs = prepare_inputs(self._processor, images=[image], prompts=chat_prompt)
        input_ids = inputs.pop("input_ids")
        inputs.pop("attention_mask", None)
        tokens = self._model.pbd_generate(
            input_ids, generation_mode="hybrid", max_tokens=2048, **inputs
        )
        return str(self._processor.decode(tokens, skip_special_tokens=False))

    @staticmethod
    def _parse_box(text: str) -> list[int] | None:
        try:
            return parse_box_response(text).bbox
        except GroundingParseError:
            return None
        return None


# ---------------------------------------------------------------------------
# Offline LocateAnything preprocessing benchmark
# ---------------------------------------------------------------------------


def _bbox_center(bbox: list[int]) -> list[int]:
    return [int(round((bbox[0] + bbox[2]) / 2)), int(round((bbox[1] + bbox[3]) / 2))]


def _validate_bbox_0_1000(bbox: list[int] | None) -> list[int] | None:
    if not bbox or len(bbox) != 4:
        return None
    try:
        parsed = parse_box_response(f"<box>{bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}</box>")
    except GroundingParseError:
        return None
    return parsed.bbox


def _bbox_iou(a: list[int] | None, b: list[int] | None) -> float | None:
    if not a or not b:
        return None
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _center_distance(a: list[int] | None, b: list[int] | None) -> float | None:
    if not a or not b:
        return None
    ca = _bbox_center(a)
    cb = _bbox_center(b)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def _fit_image(image: Image.Image, max_size: int) -> Image.Image:
    resized = image.copy()
    resized.thumbnail((max_size, max_size), Image.LANCZOS)
    return resized


def _vertical_crops(
    image: Image.Image,
    *,
    splits: int,
    overlap: float,
    max_size: int,
) -> list[tuple[Image.Image, tuple[int, int, int, int]]]:
    """Return vertical crops and their original-pixel boxes."""
    width, height = image.size
    crop_h = height / splits
    pad = crop_h * overlap
    crops: list[tuple[Image.Image, tuple[int, int, int, int]]] = []
    for idx in range(splits):
        top = max(0, int(round(idx * crop_h - pad)))
        bottom = min(height, int(round((idx + 1) * crop_h + pad)))
        box = (0, top, width, bottom)
        crop = image.crop(box)
        crop.thumbnail((max_size, max_size), Image.LANCZOS)
        crops.append((crop, box))
    return crops


def _vertical_crops_include_above(
    image: Image.Image,
    *,
    splits: int,
    include_above_ratio: float,
    max_size: int,
) -> list[tuple[Image.Image, tuple[int, int, int, int]]]:
    """Return top-to-bottom crops where each crop includes part of the previous band.

    For splits=3 and include_above_ratio=0.2, the base bands are [0, 1/3],
    [1/3, 2/3], [2/3, 1]. The latter two start 20% of one band height
    earlier, preserving context from above without adding below-context.
    """
    width, height = image.size
    band_h = height / splits
    above = band_h * include_above_ratio
    crops: list[tuple[Image.Image, tuple[int, int, int, int]]] = []
    for idx in range(splits):
        top = max(0, int(round(idx * band_h - (above if idx > 0 else 0))))
        bottom = height if idx == splits - 1 else int(round((idx + 1) * band_h))
        box = (0, top, width, bottom)
        crop = image.crop(box)
        crop.thumbnail((max_size, max_size), Image.LANCZOS)
        crops.append((crop, box))
    return crops


def _map_crop_bbox_to_full(
    bbox: list[int], crop_box: tuple[int, int, int, int], full_size: tuple[int, int]
) -> list[int]:
    left, top, right, bottom = crop_box
    full_w, full_h = full_size
    crop_w = right - left
    crop_h = bottom - top
    x1 = (left + bbox[0] / 1000 * crop_w) / full_w * 1000
    y1 = (top + bbox[1] / 1000 * crop_h) / full_h * 1000
    x2 = (left + bbox[2] / 1000 * crop_w) / full_w * 1000
    y2 = (top + bbox[3] / 1000 * crop_h) / full_h * 1000
    mapped = [int(round(v)) for v in (x1, y1, x2, y2)]
    mapped = [max(0, min(1000, v)) for v in mapped]
    return _validate_bbox_0_1000(mapped) or []


def _load_reference_bboxes(path: str | None) -> dict[str, list[int]]:
    if not path or not Path(path).exists():
        return {}
    rows = json.loads(Path(path).read_text())
    refs: dict[str, list[int]] = {}
    for row in rows:
        if (
            row.get("model") == "LocateAnything-3B-4bit"
            and float(row.get("scale", -1)) == 1.0
            and row.get("success")
            and isinstance(row.get("bbox"), list)
        ):
            refs[row["target_id"]] = [int(v) for v in row["bbox"]]
    return refs


def _parse_variants(spec: str, *, split_overlap: float, split_max_size: int) -> list[ImageVariant]:
    if split_max_size <= 0:
        raise ValueError("--split-max-size must be positive")
    if split_overlap < 0 or split_overlap >= 1:
        raise ValueError("--split-overlap must be >= 0 and < 1")
    variants: list[ImageVariant] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("full:"):
            max_size = int(item.split(":", 1)[1])
            if max_size <= 0:
                raise ValueError(f"full max_size must be positive: {item}")
            variants.append(ImageVariant(name=f"full_max{max_size}", kind="full", max_size=max_size))
        elif item.startswith("vsplit:"):
            splits = int(item.split(":", 1)[1])
            if splits < 1:
                raise ValueError(f"vsplit count must be >= 1: {item}")
            variants.append(
                ImageVariant(
                    name=f"vsplit{splits}_oracle_ov{int(split_overlap * 100):02d}_max{split_max_size}",
                    kind="vsplit",
                    splits=splits,
                    overlap=split_overlap,
                    max_size=split_max_size,
                )
            )
        elif item.startswith("vsplit_top20_parallel:"):
            splits = int(item.split(":", 1)[1])
            if splits < 1:
                raise ValueError(f"vsplit_top20_parallel count must be >= 1: {item}")
            variants.append(
                ImageVariant(
                    name=f"vsplit{splits}_top20_parallel_max{split_max_size}",
                    kind="vsplit_top20_parallel",
                    splits=splits,
                    overlap=0.20,
                    max_size=split_max_size,
                )
            )
        else:
            raise ValueError(f"Unknown variant spec: {item}")
    return variants


def _best_candidate(
    candidates: list[VariantResult], reference_bbox: list[int] | None, latency_ms: float | None = None
) -> VariantResult:
    valid = [c for c in candidates if c.success and c.bbox]
    if not valid:
        total_latency = latency_ms if latency_ms is not None else sum(c.latency_ms for c in candidates)
        calls = sum(c.calls for c in candidates)
        sizes = [size for c in candidates for size in c.input_sizes]
        error = "; ".join(c.error for c in candidates if c.error)[:160]
        return VariantResult(
            target_id=candidates[0].target_id,
            variant=candidates[0].variant,
            success=False,
            bbox=None,
            center=None,
            latency_ms=total_latency,
            calls=calls,
            input_sizes=sizes,
            reference_bbox=reference_bbox,
            error=error or "no_valid_crop",
        )
    if reference_bbox:
        valid.sort(key=lambda c: c.iou_vs_reference or 0.0, reverse=True)
    else:
        # Deployable fallback when no reference exists: prefer the largest non-empty region.
        valid.sort(key=lambda c: (c.bbox[2] - c.bbox[0]) * (c.bbox[3] - c.bbox[1]), reverse=True)
    chosen = valid[0]
    chosen.latency_ms = latency_ms if latency_ms is not None else sum(c.latency_ms for c in candidates)
    chosen.calls = sum(c.calls for c in candidates)
    chosen.input_sizes = [size for c in candidates for size in c.input_sizes]
    return chosen


def run_locateanything_preprocess_benchmark(
    *,
    model_path: str,
    image_path: str,
    output_dir: str,
    target_ids: list[str] | None,
    variants: list[ImageVariant],
    reference_json: str | None,
) -> list[VariantResult]:
    image = Image.open(image_path).convert("RGB")
    targets = TARGETS if not target_ids else [t for t in TARGETS if t["id"] in set(target_ids)]
    if target_ids and not targets:
        raise ValueError(f"No matching targets: {target_ids}")
    refs = _load_reference_bboxes(reference_json)
    runner = LocateAnythingRunner(model_path=model_path, max_size=100_000)
    runner._load()
    results: list[VariantResult] = []

    print(f"Offline image: {image_path} {image.size}")
    print(f"Variants: {[v.name for v in variants]}")
    print("Reference: previous LocateAnything scale=100 bbox (baseline, not human GT)" if refs else "Reference: none")

    for target in targets:
        reference_bbox = refs.get(target["id"])
        print(f"\n{'=' * 60}\nTarget: {target['id']} — {target['desc']} ref={reference_bbox}\n{'=' * 60}")
        for variant in variants:
            if variant.kind == "full":
                assert variant.max_size is not None
                prepared = _fit_image(image, variant.max_size)
                raw = runner.run_prepared(prepared, target)
                bbox = _validate_bbox_0_1000(raw.bbox)
                iou = _bbox_iou(bbox, reference_bbox)
                dist = _center_distance(bbox, reference_bbox)
                result = VariantResult(
                    target_id=target["id"],
                    variant=variant.name,
                    success=raw.success and bbox is not None,
                    bbox=bbox,
                    center=_bbox_center(bbox) if bbox else None,
                    latency_ms=raw.latency_ms,
                    calls=1,
                    input_sizes=[prepared.size],
                    reference_bbox=reference_bbox,
                    iou_vs_reference=iou,
                    center_distance_vs_reference=dist,
                    error=raw.error if not raw.success else ("" if bbox is not None else "invalid_bbox"),
                    raw_output=raw.raw_output,
                )
            elif variant.kind == "vsplit":
                assert variant.splits is not None and variant.max_size is not None
                candidates: list[VariantResult] = []
                for crop, crop_box in _vertical_crops(
                    image,
                    splits=variant.splits,
                    overlap=variant.overlap,
                    max_size=variant.max_size,
                ):
                    raw = runner.run_prepared(crop, target)
                    mapped_bbox = _map_crop_bbox_to_full(raw.bbox, crop_box, image.size) if raw.bbox else None
                    if mapped_bbox == []:
                        mapped_bbox = None
                    candidates.append(
                        VariantResult(
                            target_id=target["id"],
                            variant=variant.name,
                            success=raw.success and mapped_bbox is not None,
                            bbox=mapped_bbox,
                            center=_bbox_center(mapped_bbox) if mapped_bbox else None,
                            latency_ms=raw.latency_ms,
                            calls=1,
                            input_sizes=[crop.size],
                            reference_bbox=reference_bbox,
                            iou_vs_reference=_bbox_iou(mapped_bbox, reference_bbox),
                            center_distance_vs_reference=_center_distance(mapped_bbox, reference_bbox),
                            error=raw.error if not raw.success else ("" if mapped_bbox is not None else "invalid_bbox"),
                            raw_output=raw.raw_output,
                        )
                    )
                result = _best_candidate(candidates, reference_bbox)
            elif variant.kind == "vsplit_top20_parallel":
                assert variant.splits is not None and variant.max_size is not None
                crops = _vertical_crops_include_above(
                    image,
                    splits=variant.splits,
                    include_above_ratio=variant.overlap,
                    max_size=variant.max_size,
                )

                def run_crop(crop: Image.Image, crop_box: tuple[int, int, int, int]) -> VariantResult:
                    raw = runner.run_prepared(crop, target)
                    mapped_bbox = _map_crop_bbox_to_full(raw.bbox, crop_box, image.size) if raw.bbox else None
                    if mapped_bbox == []:
                        mapped_bbox = None
                    return VariantResult(
                        target_id=target["id"],
                        variant=variant.name,
                        success=raw.success and mapped_bbox is not None,
                        bbox=mapped_bbox,
                        center=_bbox_center(mapped_bbox) if mapped_bbox else None,
                        latency_ms=raw.latency_ms,
                        calls=1,
                        input_sizes=[crop.size],
                        reference_bbox=reference_bbox,
                        iou_vs_reference=_bbox_iou(mapped_bbox, reference_bbox),
                        center_distance_vs_reference=_center_distance(mapped_bbox, reference_bbox),
                        error=raw.error if not raw.success else ("" if mapped_bbox is not None else "invalid_bbox"),
                        raw_output=raw.raw_output,
                    )

                candidates = []
                wall_started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=variant.splits) as executor:
                    future_map = {
                        executor.submit(run_crop, crop, crop_box): crop_box
                        for crop, crop_box in crops
                    }
                    for future in as_completed(future_map):
                        try:
                            candidates.append(future.result())
                        except Exception as exc:
                            candidates.append(
                                VariantResult(
                                    target_id=target["id"],
                                    variant=variant.name,
                                    success=False,
                                    bbox=None,
                                    center=None,
                                    latency_ms=0,
                                    calls=1,
                                    input_sizes=[],
                                    reference_bbox=reference_bbox,
                                    error=f"parallel_error:{type(exc).__name__}",
                                )
                            )
                wall_latency = (time.perf_counter() - wall_started) * 1000
                result = _best_candidate(candidates, reference_bbox, latency_ms=wall_latency)
            else:
                raise ValueError(f"Unknown variant kind: {variant.kind}")

            results.append(result)
            status = "OK" if result.success else "FAIL"
            iou_str = f"{result.iou_vs_reference:.3f}" if result.iou_vs_reference is not None else "-"
            dist_str = f"{result.center_distance_vs_reference:.1f}" if result.center_distance_vs_reference is not None else "-"
            print(
                f"  {variant.name:<26} {status:>4} calls={result.calls:<2} "
                f"{result.latency_ms:>7.0f}ms bbox={str(result.bbox):<24} iou={iou_str:<5} cd={dist_str}"
            )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_rows = [
        {
            "target_id": r.target_id,
            "variant": r.variant,
            "success": r.success,
            "bbox": r.bbox,
            "center": r.center,
            "latency_ms": r.latency_ms,
            "calls": r.calls,
            "input_sizes": [list(size) for size in r.input_sizes],
            "reference_bbox": r.reference_bbox,
            "iou_vs_reference": r.iou_vs_reference,
            "center_distance_vs_reference": r.center_distance_vs_reference,
            "error": r.error,
        }
        for r in results
    ]
    json_path = out_path / "locateanything_preprocess_bench.json"
    json_path.write_text(json.dumps(json_rows, indent=2, ensure_ascii=False))
    print(f"\nJSON saved to {json_path}")

    print("\nAggregate by variant:")
    print("-" * 100)
    print(f"{'Variant':<28} {'OK':>8} {'AvgLat':>10} {'AvgIoU':>10} {'AvgCenterD':>12} {'AvgCalls':>9}")
    print("-" * 100)
    for variant in [v.name for v in variants]:
        rows = [r for r in results if r.variant == variant]
        ok = sum(1 for r in rows if r.success)
        lats = [r.latency_ms for r in rows if r.success]
        ious = [r.iou_vs_reference for r in rows if r.iou_vs_reference is not None]
        dists = [r.center_distance_vs_reference for r in rows if r.center_distance_vs_reference is not None]
        calls = [r.calls for r in rows]
        print(
            f"{variant:<28} {ok:>3}/{len(rows):<4} "
            f"{(sum(lats)/len(lats) if lats else 0):>9.0f}ms "
            f"{(sum(ious)/len(ious) if ious else 0):>10.3f} "
            f"{(sum(dists)/len(dists) if dists else 0):>12.1f} "
            f"{(sum(calls)/len(calls) if calls else 0):>9.1f}"
        )
    print("-" * 100)
    return results


class LFM2VLRunner:
    """LFM2.5-VL via LM Studio API (ChatML + JSON output)."""

    LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

    def __init__(self, name: str, model_id: str):
        self.name = name
        self.model_id = model_id

    def run(self, image: Image.Image, target: dict[str, Any]) -> SingleResult:
        import requests  # type: ignore

        query = target["desc"]
        prompt = (
            f"Detect all instances of: {query}. "
            f'Response must be a JSON array: [{{"label": ..., "bbox": [x1, y1, x2, y2]}}, ...]. '
            f"Coordinates are normalized to [0,1]."
        )

        buf = BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]}],
            "max_tokens": 256,
            "temperature": 0.0,
        }

        started = time.perf_counter()
        try:
            resp = requests.post(self.LM_STUDIO_URL, json=payload, timeout=120)
            if resp.status_code != 200:
                latency = (time.perf_counter() - started) * 1000
                return SingleResult(
                    model=self.name, target_id=target["id"], scale=1.0,
                    image_size=image.size, success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:100]}", latency_ms=latency,
                )
            data = resp.json()
            output = data["choices"][0]["message"]["content"]
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            return SingleResult(
                model=self.name, target_id=target["id"], scale=1.0,
                image_size=image.size, success=False,
                error=f"{type(exc).__name__}: {exc}", latency_ms=latency,
            )

        latency = (time.perf_counter() - started) * 1000
        bbox_0_1000 = self._parse_json_box(output)
        if bbox_0_1000 is None:
            return SingleResult(
                model=self.name, target_id=target["id"], scale=1.0,
                image_size=image.size, success=False,
                raw_output=output[:200],
                error=f"parse_failed: {output[:100]}", latency_ms=latency,
            )
        center = [
            int(round((bbox_0_1000[0] + bbox_0_1000[2]) / 2)),
            int(round((bbox_0_1000[1] + bbox_0_1000[3]) / 2)),
        ]
        return SingleResult(
            model=self.name, target_id=target["id"], scale=1.0,
            image_size=image.size, success=True,
            bbox=bbox_0_1000, center=center,
            raw_output=output[:200], latency_ms=latency,
        )

    @staticmethod
    def _parse_json_box(text: str) -> list[int] | None:
        """Parse JSON bbox, convert 0-1 -> 0-1000."""
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        if not isinstance(data, list) or len(data) == 0:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        bbox = first.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return None
        return [int(round(v * 1000)) for v in bbox]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(
    runners: list[Any],
    targets: list[dict[str, Any]],
    scales: list[float],
    device_id: str | None = None,
    output_dir: str | None = None,
) -> BenchmarkReport:
    screen_w, screen_h = adb_screen_size(device_id)
    report = BenchmarkReport(screen_size=(screen_w, screen_h))

    out_path = Path(output_dir) if output_dir else None
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)

    for target in targets:
        print(f"\n{'=' * 60}")
        print(f"Target: {target['id']} — {target['desc']}")
        print(f"{'=' * 60}")

        raw_png = adb_screencap(device_id)
        original = Image.open(BytesIO(raw_png)).convert("RGB")

        for scale in scales:
            image = resize_image(original, scale)
            img_w, img_h = image.size
            print(f"\n  Scale {scale:.0%} -> {img_w}x{img_h}")

            if out_path:
                img_name = f"{target['id']}_scale{int(scale * 100):03d}.png"
                image.save(out_path / img_name)

            for runner in runners:
                result = runner.run(image, target)
                result.scale = scale
                result.image_size = (img_w, img_h)
                report.results.append(result)

                status = "OK" if result.success else "FAIL"
                bbox_str = str(result.bbox) if result.bbox else "-"
                print(
                    f"    {runner.name:<30} {status:>4}  "
                    f"{result.latency_ms:>7.0f}ms  bbox={bbox_str}"
                )
                if result.error:
                    print(f"      Error: {result.error[:120]}")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Grounding model benchmark via MLX")
    parser.add_argument("--device", "-d", help="ADB device ID")
    parser.add_argument("--output-dir", "-o", default="bench_output",
                        help="Output directory for screenshots and JSON")
    parser.add_argument("--locateanything-path", default="models/LocateAnything-3B-4bit")
    parser.add_argument("--skip-locateanything", action="store_true")
    parser.add_argument("--skip-lfm450m", action="store_true")
    parser.add_argument("--skip-lfm1b", action="store_true")
    parser.add_argument("--targets", nargs="*",
                        help="Specific target IDs (default: all)")
    parser.add_argument("--scales", nargs="*", type=float,
                        default=[1.0, 0.5, 0.25, 0.125])
    parser.add_argument("--json", action="store_true",
                        help="Also save results as JSON")
    parser.add_argument("--locateanything-preprocess-bench", action="store_true",
                        help="Offline LocateAnything benchmark for full-image compression and vertical splitting")
    parser.add_argument("--image", default="test_screenshot.png",
                        help="Offline benchmark image path")
    parser.add_argument("--reference-json", default="bench_output/bench_results.json",
                        help="Reference bbox JSON from previous benchmark")
    parser.add_argument("--preprocess-variants",
                        default="full:1280,full:960,full:720,full:512,full:384,vsplit:2,vsplit:3,vsplit:4",
                        help="Comma-separated variants, e.g. full:720,vsplit:3")
    parser.add_argument("--split-overlap", type=float, default=0.15,
                        help="Vertical split overlap ratio per segment")
    parser.add_argument("--split-max-size", type=int, default=1280,
                        help="Max input size for each split crop")
    args = parser.parse_args()

    if args.locateanything_preprocess_bench:
        variants = _parse_variants(
            args.preprocess_variants,
            split_overlap=args.split_overlap,
            split_max_size=args.split_max_size,
        )
        run_locateanything_preprocess_benchmark(
            model_path=args.locateanything_path,
            image_path=args.image,
            output_dir=args.output_dir,
            target_ids=args.targets,
            variants=variants,
            reference_json=args.reference_json,
        )
        return

    runners = []
    if not args.skip_locateanything:
        if Path(args.locateanything_path).exists():
            runners.append(LocateAnythingRunner(args.locateanything_path))
        else:
            print(f"[WARN] LocateAnything not found: {args.locateanything_path}")
    if not args.skip_lfm450m:
        runners.append(LFM2VLRunner("LFM2.5-VL-450M-4bit", "lfm2.5-vl-450m-mlx"))
    if not args.skip_lfm1b:
        runners.append(LFM2VLRunner("LFM2.5-VL-1.6B-4bit", "lfm2.5-vl-1.6b-mlx"))

    if not runners:
        print("ERROR: No models available.")
        sys.exit(1)

    targets = TARGETS
    if args.targets:
        targets = [t for t in TARGETS if t["id"] in args.targets]
        if not targets:
            print(f"ERROR: No matching targets: {args.targets}")
            sys.exit(1)

    print(f"Models:  {[r.name for r in runners]}")
    print(f"Targets: {[t['id'] for t in targets]}")
    print(f"Scales:  {args.scales}")
    print(f"Total runs: {len(runners)} × {len(targets)} × {len(args.scales)} = "
          f"{len(runners) * len(targets) * len(args.scales)}")

    report = run_benchmark(
        runners=runners, targets=targets, scales=args.scales,
        device_id=args.device, output_dir=args.output_dir,
    )

    print("\n")
    print(report.summary_table())
    print("\n")
    print(report.aggregate_table())

    if args.json:
        json_results = []
        for r in report.results:
            json_results.append({
                "model": r.model,
                "target_id": r.target_id,
                "scale": r.scale,
                "image_size": list(r.image_size),
                "success": r.success,
                "bbox": r.bbox,
                "center": r.center,
                "error": r.error,
                "latency_ms": r.latency_ms,
            })
        json_path = Path(args.output_dir) / "bench_results.json"
        json_path.write_text(json.dumps(json_results, indent=2, ensure_ascii=False))
        print(f"\nJSON saved to {json_path}")


if __name__ == "__main__":
    main()
