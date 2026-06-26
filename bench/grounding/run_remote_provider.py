"""CLI runner for benchmarking OpenAI-compatible remote grounding providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from bench.grounding.datasets import DatasetFilters, SamplingConfig, build_post_training_manifest, read_manifest, write_manifest
from bench.grounding.reporting import build_summary, enrich_prediction, write_jsonl
from bench.grounding.run_locateanything import load_screenshot
from phone_agent.grounding.provider import MarkProviderHint
from phone_agent.grounding.remote_openai import DEFAULT_REMOTE_GROUNDING_MODEL, RemoteOpenAIGroundingProvider


def run_case(provider: RemoteOpenAIGroundingProvider, case: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    image_path = Path(str(case.get("image") or ""))
    prompt = str(case.get("prompt") or "").strip()
    prediction: dict[str, Any] = {
        "case_id": str(case.get("id")),
        "image": str(image_path),
        "prompt_hash": _safe_prompt_hash(prompt),
        "prompt_length": len(prompt),
        "provider": provider.name,
        "model": provider.model,
    }
    if not image_path.is_file():
        return {**prediction, "success": False, "parsed": False, "runner_error": "missing_image", "latency_ms": None}
    if not prompt:
        return {**prediction, "success": False, "parsed": False, "runner_error": "missing_prompt", "latency_ms": None}
    try:
        screenshot, binding = load_screenshot(image_path)
        result = provider.provide_marks(screenshot, binding, hints=[MarkProviderHint(text=prompt)], timeout=timeout)
    except Exception as exc:
        return {**prediction, "success": False, "parsed": False, "runner_error": type(exc).__name__, "latency_ms": None}
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


def _safe_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16] if prompt else ""


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
        write_manifest(Path(args.manifest_output), cases, metadata={"source": str(args.post_training_data), "filters": filters.__dict__, "sampling": sampling.__dict__})
    return cases


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--base-url", default=os.getenv("PHONE_AGENT_REMOTE_GROUNDING_BASE_URL") or "https://api.stepfun.com/v1")
    parser.add_argument("--api-key-env", default="PHONE_AGENT_REMOTE_GROUNDING_API_KEY")
    parser.add_argument("--model", default=os.getenv("PHONE_AGENT_REMOTE_GROUNDING_MODEL", DEFAULT_REMOTE_GROUNDING_MODEL))
    parser.add_argument("--max-size", type=int, default=int(os.getenv("PHONE_AGENT_REMOTE_GROUNDING_MAX_SIZE", "960")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PHONE_AGENT_REMOTE_GROUNDING_TIMEOUT", "30")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cases = build_cases_from_args(args)
    if not cases:
        raise SystemExit("no benchmark cases selected")
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise SystemExit(f"missing API key env: {args.api_key_env}")
    provider = RemoteOpenAIGroundingProvider(base_url=args.base_url, api_key=api_key, model=args.model, max_size=args.max_size, timeout=args.timeout)
    print(f"Running remote grounding on {len(cases)} cases model={args.model} max_size={args.max_size}", flush=True)
    predictions: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        prediction = enrich_prediction(run_case(provider, case, timeout=args.timeout), case)
        predictions.append(prediction)
        print(
            f"[{index}/{len(cases)}] ok={prediction.get('success')} hit={prediction.get('center_hit')} "
            f"iou={float(prediction.get('iou') or 0.0):.3f} lat={prediction.get('latency_ms')}ms "
            f"type={prediction.get('target_type')} prompt_hash={prediction.get('prompt_hash')}",
            flush=True,
        )
    output_path = Path(args.output)
    write_jsonl(output_path, predictions)
    summary = build_summary(predictions, metadata={"model": args.model, "max_size": args.max_size, "limit": args.limit, "seed": args.seed, "sampling": args.sampling, "output": str(output_path)})
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
