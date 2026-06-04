import json
import importlib.util
import sys
from pathlib import Path

RUN_EVAL_PATH = Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
SPEC = importlib.util.spec_from_file_location("run_eval_module", RUN_EVAL_PATH)
assert SPEC is not None and SPEC.loader is not None
run_eval_module = importlib.util.module_from_spec(SPEC)
sys.modules["run_eval_module"] = run_eval_module
SPEC.loader.exec_module(run_eval_module)

load_tasks = run_eval_module.load_tasks
parse_args = run_eval_module.parse_args
run_eval = run_eval_module.run_eval


def test_load_tasks_reads_task_schema(tmp_path) -> None:
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "task": "do thing",
                    "category": "smoke",
                    "expected_app": "FakeApp",
                    "max_steps": 3,
                }
            ]
        ),
        encoding="utf-8",
    )

    tasks = load_tasks(tasks_file)

    assert tasks[0].id == "t1"
    assert tasks[0].task == "do thing"
    assert tasks[0].expected_app == "FakeApp"
    assert tasks[0].max_steps == 3


def test_dry_run_eval_outputs_stable_json_shape(monkeypatch, tmp_path) -> None:
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(
        json.dumps(
            [
                {"id": "smoke", "task": "finish", "category": "smoke", "max_steps": 1},
                {"id": "hitl", "task": "confirm", "category": "hitl", "max_steps": 1},
                {"id": "failed", "task": "fail", "category": "failed", "max_steps": 1},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv", ["run_eval.py", "--dry-run", "--tasks", str(tasks_file)]
    )

    output = run_eval(parse_args())

    assert output["summary"]["total"] == 3
    assert output["summary"]["success"] == 3
    assert output["summary"]["hitl_count"] == 1
    assert output["summary"]["retry_count"] == 1
    assert output["summary"]["failure_cause_histogram"] == {"element_not_found": 1}
    assert output["summary"]["grounding_failure_histogram"] == {}
    assert output["summary"]["grounding_count"] == 0
    assert output["summary"]["context_mode"] == "observe"
    assert output["summary"]["context_strategy"] == "observe_only"
    assert output["summary"]["prompt_version"] == "context_harness_v1"
    assert output["summary"]["selected_section_counts"] == {"screen_belief": 3}
    assert output["summary"]["messages_before"] == 6
    assert output["summary"]["messages_after"] == 6
    assert output["summary"]["failure_memory_hit_count"] == 1
    assert output["summary"]["repeated_failure_count"] == 1
    assert output["summary"]["dry_run"] is True
    assert output["summary"]["output_mode"] == "json_schema"
    first = output["results"][0]
    assert {
        "task_id",
        "task",
        "category",
        "expected_app",
        "max_steps",
        "success",
        "steps",
        "duration",
        "final_message",
        "error",
        "hitl_count",
        "trace_id",
        "context_mode",
        "context_strategy",
        "prompt_version",
        "selected_sections",
        "messages_before",
        "messages_after",
        "message_chars_before",
        "message_chars_after",
        "context_block_chars",
        "context_truncated",
        "failure_memory_hit_count",
        "repeated_failure_count",
    } <= set(first)


def test_parse_args_accepts_context_mode(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_eval.py", "--dry-run", "--context-mode", "inject"])

    args = parse_args()

    assert args.context_mode == "inject"


def test_parse_args_rejects_invalid_output_mode_env(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_AGENT_OUTPUT_MODE", "bad")
    monkeypatch.setattr("sys.argv", ["run_eval.py", "--dry-run"])

    try:
        parse_args()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("parse_args should reject invalid PHONE_AGENT_OUTPUT_MODE")
