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
        context_block_chars=context_block_chars,
        context_truncated=False,
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
            context_mode=args.context_mode,
        ),
    )
    return agent.run_structured(task.task)


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
    total_retries = 0
    total_context_chars = 0
    context_truncated_count = 0
    total_failure_memory_hits = 0
    total_repeated_failures = 0
    for item in records:
        total_retries += int(item.get("retry_count") or 0)
        total_context_chars += int(item.get("context_block_chars") or 0)
        context_truncated_count += 1 if item.get("context_truncated") else 0
        total_failure_memory_hits += int(item.get("failure_memory_hit_count") or 0)
        total_repeated_failures += int(item.get("repeated_failure_count") or 0)
        cause = item.get("failure_cause")
        if cause:
            failure_cause_histogram[str(cause)] = failure_cause_histogram.get(str(cause), 0) + 1

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
            "context_mode": args.context_mode,
            "context_block_chars": total_context_chars,
            "avg_context_block_chars": total_context_chars / len(records) if records else 0.0,
            "context_truncated_count": context_truncated_count,
            "failure_memory_hit_count": total_failure_memory_hits,
            "repeated_failure_count": total_repeated_failures,
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
        "--output-mode",
        choices=["text_dsl", "json_schema", "tool_calls", "auto"],
        default=os.getenv("PHONE_AGENT_OUTPUT_MODE", "text_dsl"),
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
    parser.add_argument("--trace-dir", default=".traces", help="Local JSONL trace dir")
    parser.add_argument("--no-trace", action="store_true", help="Disable agent tracing")
    args = parser.parse_args()
    if args.output_mode not in {"text_dsl", "json_schema", "tool_calls", "auto"}:
        parser.error("--output-mode must be one of: text_dsl, json_schema, tool_calls, auto")
    return args


def main() -> None:
    """CLI entrypoint."""
    print(json.dumps(run_eval(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
