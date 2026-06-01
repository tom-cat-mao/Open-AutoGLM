import json
import importlib.util
import sys
from pathlib import Path

RUN_EVAL_PATH = Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
SPEC = importlib.util.spec_from_file_location("run_eval_trace_module", RUN_EVAL_PATH)
assert SPEC is not None and SPEC.loader is not None
run_eval_module = importlib.util.module_from_spec(SPEC)
sys.modules["run_eval_trace_module"] = run_eval_module
SPEC.loader.exec_module(run_eval_module)

parse_args = run_eval_module.parse_args
run_eval = run_eval_module.run_eval


def test_dry_run_eval_includes_trace_file(monkeypatch, tmp_path) -> None:
    tasks_file = tmp_path / "tasks.json"
    trace_dir = tmp_path / "traces"
    tasks_file.write_text(
        json.dumps([{"id": "smoke", "task": "finish", "category": "smoke"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_eval.py",
            "--dry-run",
            "--tasks",
            str(tasks_file),
            "--trace-dir",
            str(trace_dir),
        ],
    )

    output = run_eval(parse_args())

    result = output["results"][0]
    assert result["trace_id"]
    assert result["trace_path"]
    assert Path(result["trace_path"]).exists()
    assert output["summary"]["trace_dir"] == str(trace_dir)
