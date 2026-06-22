#!/usr/bin/env python3
"""Local evaluation harness for PhoneAgent structured runs."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phone_agent.agent import AgentConfig, PhoneAgent, RunResult
from phone_agent.graph.context import DEFAULT_CONTEXT_MODE, normalize_context_mode
from phone_agent.graph.trace import JsonlTraceWriter, sanitize_for_trace
from phone_agent.model import ModelConfig

DEFAULT_TASKS_PATH = Path(__file__).with_name("tasks.json")
DEFAULT_MODEL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Open-AutoGLM/0.1"
)


@dataclass
class EvalTask:
    """A single evaluation task definition."""

    id: str
    task: str
    category: str = "smoke"
    expected_app: str | None = None
    max_steps: int = 10

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalTask":
        """Create EvalTask from JSON task data."""
        return cls(
            id=str(data["id"]),
            task=str(data["task"]),
            category=str(data.get("category", "smoke")),
            expected_app=data.get("expected_app"),
            max_steps=int(data.get("max_steps", 10)),
        )


def load_tasks(path: Path) -> list[EvalTask]:
    """Load eval tasks from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Eval task file must contain a JSON list")
    return [EvalTask.from_dict(item) for item in data]


def run_dry_task(
    task: EvalTask, trace_dir: str = ".traces", context_mode: str = DEFAULT_CONTEXT_MODE
) -> RunResult:
    """Run a deterministic dry-run task without model or device dependencies."""
    started_at = time.perf_counter()
    hitl_count = 1 if task.category == "hitl" else 0
    failure_cause = "element_not_found" if task.category == "failed" else None
    retry_count = 1 if failure_cause else 0
    trace_id = f"dry-{uuid.uuid4()}"
    trace_writer = JsonlTraceWriter(trace_id=trace_id, trace_dir=trace_dir)
    trace_writer.emit(
        "eval",
        "dry_run_start",
        0,
        {"task_id": task.id, "task": task.task, "category": task.category},
    )
    mode = normalize_context_mode(context_mode)
    context_block_chars = 0 if mode != "inject" else 120
    trace_writer.emit(
        "eval",
        "dry_run_end",
        1,
        {
            "success": True,
            "context_mode": mode,
            "context_strategy": "off" if mode == "off" else ("inject_redacted_block" if mode == "inject" else "observe_only"),
            "prompt_version": "context_harness_v1",
            "selected_sections": ["screen_belief"] if mode != "off" else [],
            "messages_before": 2,
            "messages_after": 2,
            "message_chars_before": 240 + context_block_chars,
            "message_chars_after": 240 + context_block_chars,
            "approx_tokens_before": (240 + context_block_chars + 3) // 4,
            "approx_tokens_after": (240 + context_block_chars + 3) // 4,
            "context_block_chars": context_block_chars,
        },
    )
    return RunResult(
        success=True,
        finished=True,
        steps=1,
        duration=time.perf_counter() - started_at,
        final_message="Dry-run task completed",
        error=None,
        hitl_count=hitl_count,
        trace_id=trace_id,
        trace_path=str(trace_writer.path),
        failure_cause=failure_cause,
        retry_count=retry_count,
        context_mode=mode,
        context_strategy="off" if mode == "off" else ("inject_redacted_block" if mode == "inject" else "observe_only"),
        prompt_version="context_harness_v1",
        selected_sections=["screen_belief"] if mode != "off" else [],
        context_block_chars=context_block_chars,
        context_truncated=False,
        messages_before=2,
        messages_after=2,
        message_chars_before=240 + context_block_chars,
        message_chars_after=240 + context_block_chars,
        approx_tokens_before=(240 + context_block_chars + 3) // 4,
        approx_tokens_after=(240 + context_block_chars + 3) // 4,
        failure_memory_hit_count=1 if failure_cause and mode != "off" else 0,
        repeated_failure_count=1 if task.category == "failed" and mode != "off" else 0,
    )


def run_agent_task(task: EvalTask, args: argparse.Namespace) -> RunResult:
    """Run a task through PhoneAgent.run_structured()."""
    agent = PhoneAgent(
        model_config=ModelConfig(
            base_url=args.base_url,
            model_name=args.model,
            api_key=args.apikey,
            timeout=args.model_timeout,
            max_retries=args.model_max_retries,
            default_headers=build_model_headers(args),
            stream=args.stream,
            extra_body=args.model_extra_body_dict,
            thinking_mode=args.thinking_mode,
            thinking_param=args.thinking_param,
            trace_raw_model_response=args.trace_raw_model_response,
            lang=args.lang,
            output_mode=args.output_mode,
        ),
        agent_config=AgentConfig(
            max_steps=task.max_steps,
            device_id=args.device_id,
            lang=args.lang,
            verbose=not args.quiet,
            trace_enabled=not args.no_trace,
            trace_dir=args.trace_dir,
            trace_raw_model_response=args.trace_raw_model_response,
            context_mode=args.context_mode,
            grounding_provider_name=args.grounding_provider,
            accessibility_timeout=args.accessibility_timeout,
            accessibility_max_marks=args.accessibility_max_marks,
            locateanything_context_max_chars=args.locateanything_context_max_chars,
            locateanything_structure_mode=args.locateanything_structure_mode,
            locateanything_max_visual_candidates=args.locateanything_max_visual_candidates,
            locateanything_visual_category_budget=args.locateanything_visual_category_budget,
            locateanything_max_structure_calls=args.locateanything_max_structure_calls,
        ),
    )
    return agent.run_structured(task.task)


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse boolean env-style values."""

    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_json_object(value: str | None, name: str) -> dict[str, Any]:
    """Parse a JSON object from CLI/env text."""

    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def parse_env_headers(value: str | None) -> dict[str, str]:
    """Parse optional OpenAI-compatible HTTP headers from env text."""

    if not value:
        return {}
    stripped = value.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("PHONE_AGENT_HTTP_HEADERS JSON must be an object")
        return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(k).strip()}
    headers: dict[str, str] = {}
    for item in stripped.replace("\n", ",").split(","):
        if not item.strip():
            continue
        if ":" in item:
            key, value_item = item.split(":", 1)
        elif "=" in item:
            key, value_item = item.split("=", 1)
        else:
            raise ValueError("PHONE_AGENT_HTTP_HEADERS entries must use Header=Value or Header:Value")
        key = key.strip()
        if key:
            headers[key] = value_item.strip()
    return headers


def build_model_headers(args: argparse.Namespace) -> dict[str, str]:
    """Build model request headers to match main.py runtime behavior."""

    headers = parse_env_headers(os.getenv("PHONE_AGENT_HTTP_HEADERS"))
    user_agent = args.user_agent or os.getenv("PHONE_AGENT_USER_AGENT") or DEFAULT_MODEL_USER_AGENT
    if user_agent:
        headers["User-Agent"] = user_agent
    cf_client_id = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID")
    cf_client_secret = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET")
    if bool(cf_client_id) != bool(cf_client_secret):
        raise ValueError("PHONE_AGENT_CF_ACCESS_CLIENT_ID and PHONE_AGENT_CF_ACCESS_CLIENT_SECRET must be configured together")
    if cf_client_id and cf_client_secret:
        headers["CF-Access-Client-Id"] = cf_client_id
        headers["CF-Access-Client-Secret"] = cf_client_secret
    return headers


def result_record(task: EvalTask, result: RunResult) -> dict[str, Any]:
    """Build a stable JSON record for one eval result."""
    record = sanitize_for_trace(result.to_dict())
    record.update(
        sanitize_for_trace({
            "task_id": task.id,
            "task": task.task,
            "category": task.category,
            "expected_app": task.expected_app,
            "max_steps": task.max_steps,
        })
    )
    return record


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    """Run all eval tasks and return JSON-serializable results."""
    tasks = load_tasks(Path(args.tasks))
    records = []
    for task in tasks:
        result = (
            run_dry_task(task, args.trace_dir, args.context_mode)
            if args.dry_run
            else run_agent_task(task, args)
        )
        records.append(result_record(task, result))

    success_count = sum(1 for item in records if item["success"])
    total_steps = sum(int(item["steps"]) for item in records)
    total_duration = sum(float(item["duration"]) for item in records)
    total_hitl = sum(int(item["hitl_count"]) for item in records)
    failure_cause_histogram: dict[str, int] = {}
    grounding_failure_histogram: dict[str, int] = {}
    total_retries = 0
    total_grounding_latency_ms = 0
    grounding_count = 0
    total_context_chars = 0
    context_truncated_count = 0
    total_messages_before = 0
    total_messages_after = 0
    total_message_chars_before = 0
    total_message_chars_after = 0
    total_failure_memory_hits = 0
    total_repeated_failures = 0
    verifier_status_counts: dict[str, int] = {}
    selected_section_counts: dict[str, int] = {}
    for item in records:
        total_retries += int(item.get("retry_count") or 0)
        total_context_chars += int(item.get("context_block_chars") or 0)
        context_truncated_count += 1 if item.get("context_truncated") else 0
        total_messages_before += int(item.get("messages_before") or 0)
        total_messages_after += int(item.get("messages_after") or 0)
        total_message_chars_before += int(item.get("message_chars_before") or 0)
        total_message_chars_after += int(item.get("message_chars_after") or 0)
        total_failure_memory_hits += int(item.get("failure_memory_hit_count") or 0)
        total_repeated_failures += int(item.get("repeated_failure_count") or 0)
        for section in item.get("selected_sections") or []:
            section_id = str(section)
            selected_section_counts[section_id] = selected_section_counts.get(section_id, 0) + 1
        verifier_status = item.get("verifier_status")
        if verifier_status:
            verifier_status_counts[str(verifier_status)] = verifier_status_counts.get(str(verifier_status), 0) + 1
        cause = item.get("failure_cause")
        if cause:
            failure_cause_histogram[str(cause)] = failure_cause_histogram.get(str(cause), 0) + 1
        grounding_failure = item.get("grounding_failure_code")
        if grounding_failure:
            grounding_failure_histogram[str(grounding_failure)] = grounding_failure_histogram.get(str(grounding_failure), 0) + 1
        if item.get("grounding_latency_ms") is not None:
            grounding_count += 1
            total_grounding_latency_ms += int(item.get("grounding_latency_ms") or 0)

    return {
        "summary": {
            "total": len(records),
            "success": success_count,
            "success_rate": success_count / len(records) if records else 0.0,
            "avg_steps": total_steps / len(records) if records else 0.0,
            "avg_duration": total_duration / len(records) if records else 0.0,
            "hitl_count": total_hitl,
            "retry_count": total_retries,
            "failure_cause_histogram": failure_cause_histogram,
            "grounding_failure_histogram": grounding_failure_histogram,
            "grounding_count": grounding_count,
            "avg_grounding_latency_ms": total_grounding_latency_ms / grounding_count if grounding_count else 0.0,
            "context_mode": args.context_mode,
            "context_strategy": records[0].get("context_strategy") if records else "unknown",
            "prompt_version": records[0].get("prompt_version") if records else "context_harness_v1",
            "selected_section_counts": selected_section_counts,
            "context_block_chars": total_context_chars,
            "avg_context_block_chars": total_context_chars / len(records) if records else 0.0,
            "context_truncated_count": context_truncated_count,
            "messages_before": total_messages_before,
            "messages_after": total_messages_after,
            "message_chars_before": total_message_chars_before,
            "message_chars_after": total_message_chars_after,
            "failure_memory_hit_count": total_failure_memory_hits,
            "repeated_failure_count": total_repeated_failures,
            "verifier_status_counts": verifier_status_counts,
            "dry_run": args.dry_run,
            "trace_dir": args.trace_dir,
            "output_mode": args.output_mode,
        },
        "results": records,
    }


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run PhoneAgent eval tasks")
    parser.add_argument(
        "--tasks", default=str(DEFAULT_TASKS_PATH), help="Path to tasks JSON"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run deterministic local smoke eval"
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000/v1", help="Model API base URL"
    )
    parser.add_argument("--model", default="autoglm-phone-9b", help="Model name")
    parser.add_argument("--apikey", default="EMPTY", help="Model API key")
    parser.add_argument(
        "--user-agent",
        default=os.getenv("PHONE_AGENT_USER_AGENT"),
        help="Optional User-Agent header for model API requests",
    )
    parser.add_argument(
        "--model-timeout",
        type=float,
        default=float(os.getenv("PHONE_AGENT_MODEL_TIMEOUT", "60")),
        help="Model API timeout in seconds",
    )
    parser.add_argument(
        "--model-max-retries",
        type=int,
        default=int(os.getenv("PHONE_AGENT_MODEL_MAX_RETRIES", "2")),
        help="Model API max retries",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_STREAM"), False),
        help="Enable streaming model responses",
    )
    parser.add_argument(
        "--model-extra-body",
        default=os.getenv("PHONE_AGENT_MODEL_EXTRA_BODY"),
        help="Extra JSON object merged into model API request body",
    )
    parser.add_argument(
        "--thinking-mode",
        choices=["auto", "on", "off"],
        default=os.getenv("PHONE_AGENT_THINKING_MODE", "auto"),
        help="Provider thinking mode control",
    )
    parser.add_argument(
        "--thinking-param",
        choices=["enable_thinking", "chat_template_kwargs"],
        default=os.getenv("PHONE_AGENT_THINKING_PARAM", "enable_thinking"),
        help="How to pass thinking mode to the provider",
    )
    parser.add_argument(
        "--output-mode",
        choices=["json_schema", "tool_calls", "auto"],
        default=os.getenv("PHONE_AGENT_OUTPUT_MODE", "json_schema"),
        help="Model output mode",
    )
    parser.add_argument("--device-id", default=None, help="ADB device id")
    parser.add_argument(
        "--lang", choices=["cn", "en"], default="cn", help="Prompt language"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress agent verbose output"
    )
    parser.add_argument(
        "--context-mode",
        choices=["off", "observe", "inject"],
        default=DEFAULT_CONTEXT_MODE,
        help="Context harness mode",
    )
    parser.add_argument(
        "--grounding-provider",
        choices=[
            "off",
            "fake",
            "locateanything",
            "locateanything_mlx",
            "mlx",
            "accessibility",
            "accessibility_tree",
            "uiautomator",
            "hybrid",
            "accessibility_locateanything",
            "uiautomator_locateanything",
        ],
        default=os.getenv("PHONE_AGENT_GROUNDING_PROVIDER", "hybrid"),
        help="Grounding mark provider",
    )
    parser.add_argument(
        "--accessibility-timeout",
        type=float,
        default=float(os.getenv("PHONE_AGENT_ACCESSIBILITY_TIMEOUT", "3.0")),
        help="UiAutomator accessibility dump timeout",
    )
    parser.add_argument(
        "--accessibility-max-marks",
        type=int,
        default=int(os.getenv("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS", "80")),
        help="Maximum accessibility marks per screen",
    )
    parser.add_argument(
        "--locateanything-context-max-chars",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS", "0")),
        help="Bounded LocateAnything context budget",
    )
    parser.add_argument(
        "--locateanything-structure-mode",
        choices=["off", "target", "screen"],
        default=None,
        help="Optional LocateAnything visual structure mode",
    )
    parser.add_argument(
        "--locateanything-max-visual-candidates",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_VISUAL_CANDIDATES", "30")),
        help="Maximum visual sidecar candidates emitted by LocateAnything structure mode",
    )
    parser.add_argument(
        "--locateanything-visual-category-budget",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_VISUAL_CATEGORY_BUDGET", "5")),
        help="Maximum bounded visual categories queried in screen structure mode",
    )
    parser.add_argument(
        "--locateanything-max-structure-calls",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_STRUCTURE_CALLS", "5")),
        help="Maximum LocateAnything calls used for screen structure sidecar generation",
    )
    parser.add_argument("--trace-dir", default=".traces", help="Local JSONL trace dir")
    parser.add_argument("--no-trace", action="store_true", help="Disable agent tracing")
    parser.add_argument(
        "--trace-raw-model-response",
        action="store_true",
        default=parse_bool(os.getenv("PHONE_AGENT_TRACE_RAW_MODEL_RESPONSE"), False),
        help="Write raw model response text into local trace metadata for debugging",
    )
    args = parser.parse_args()
    if args.output_mode not in {"json_schema", "tool_calls", "auto"}:
        parser.error("--output-mode must be one of: json_schema, tool_calls, auto")
    if args.model_timeout <= 0:
        parser.error("--model-timeout must be positive")
    if args.model_max_retries < 0:
        parser.error("--model-max-retries must be non-negative")
    try:
        args.model_extra_body_dict = parse_json_object(
            args.model_extra_body, "--model-extra-body / PHONE_AGENT_MODEL_EXTRA_BODY"
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> None:
    """CLI entrypoint."""
    print(json.dumps(run_eval(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
