#!/usr/bin/env python3
"""Local evaluation harness for PhoneAgent structured runs."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phone_agent.agent import AgentConfig, PhoneAgent, RunResult
from phone_agent.graph.trace import JsonlTraceWriter
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


def run_dry_task(task: EvalTask, trace_dir: str = ".traces") -> RunResult:
    """Run a deterministic dry-run task without model or device dependencies."""
    started_at = time.perf_counter()
    hitl_count = 1 if task.category == "hitl" else 0
    trace_id = f"dry-{uuid.uuid4()}"
    trace_writer = JsonlTraceWriter(trace_id=trace_id, trace_dir=trace_dir)
    trace_writer.emit(
        "eval",
        "dry_run_start",
        0,
        {"task_id": task.id, "task": task.task, "category": task.category},
    )
    trace_writer.emit("eval", "dry_run_end", 1, {"success": True})
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
    )


def run_agent_task(task: EvalTask, args: argparse.Namespace) -> RunResult:
    """Run a task through PhoneAgent.run_structured()."""
    agent = PhoneAgent(
        model_config=ModelConfig(
            base_url=args.base_url,
            model_name=args.model,
            api_key=args.apikey,
            lang=args.lang,
        ),
        agent_config=AgentConfig(
            max_steps=task.max_steps,
            device_id=args.device_id,
            lang=args.lang,
            verbose=not args.quiet,
            trace_enabled=not args.no_trace,
            trace_dir=args.trace_dir,
        ),
    )
    return agent.run_structured(task.task)


def result_record(task: EvalTask, result: RunResult) -> dict[str, Any]:
    """Build a stable JSON record for one eval result."""
    record = result.to_dict()
    record.update(
        {
            "task_id": task.id,
            "task": task.task,
            "category": task.category,
            "expected_app": task.expected_app,
            "max_steps": task.max_steps,
        }
    )
    return record


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    """Run all eval tasks and return JSON-serializable results."""
    tasks = load_tasks(Path(args.tasks))
    records = []
    for task in tasks:
        result = (
            run_dry_task(task, args.trace_dir)
            if args.dry_run
            else run_agent_task(task, args)
        )
        records.append(result_record(task, result))

    success_count = sum(1 for item in records if item["success"])
    total_steps = sum(int(item["steps"]) for item in records)
    total_duration = sum(float(item["duration"]) for item in records)
    total_hitl = sum(int(item["hitl_count"]) for item in records)

    return {
        "summary": {
            "total": len(records),
            "success": success_count,
            "success_rate": success_count / len(records) if records else 0.0,
            "avg_steps": total_steps / len(records) if records else 0.0,
            "avg_duration": total_duration / len(records) if records else 0.0,
            "hitl_count": total_hitl,
            "dry_run": args.dry_run,
            "trace_dir": args.trace_dir,
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
    parser.add_argument("--device-id", default=None, help="ADB device id")
    parser.add_argument(
        "--lang", choices=["cn", "en"], default="cn", help="Prompt language"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress agent verbose output"
    )
    parser.add_argument("--trace-dir", default=".traces", help="Local JSONL trace dir")
    parser.add_argument("--no-trace", action="store_true", help="Disable agent tracing")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    print(json.dumps(run_eval(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
