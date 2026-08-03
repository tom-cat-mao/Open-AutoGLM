"""F2.0/F2.5: eval and agent takeover-interrupt attribution + continuation metrics."""

import json
import importlib.util
import sys
from pathlib import Path

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from phone_agent.agent import AgentConfig, PhoneAgent, RunResult

RUN_EVAL_PATH = Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
SPEC = importlib.util.spec_from_file_location("run_eval_module", RUN_EVAL_PATH)
assert SPEC is not None and SPEC.loader is not None
run_eval_module = importlib.util.module_from_spec(SPEC)
sys.modules["run_eval_module"] = run_eval_module
SPEC.loader.exec_module(run_eval_module)


class _RaisingGraph:
    def __init__(self, interrupt: GraphInterrupt) -> None:
        self.interrupt = interrupt

    def invoke(self, initial_state, config):
        raise self.interrupt


def _takeover_interrupt(message: str = "需要登录或验证码") -> GraphInterrupt:
    return GraphInterrupt(interrupts=(Interrupt(value={"type": "takeover", "message": message}),))


def test_run_structured_converts_takeover_interrupt_to_clean_attribution(
    monkeypatch,
) -> None:
    """F2.0: a GraphInterrupt from the takeover node is a clean terminal result —
    success=False, failure_cause=takeover, final_message=接管原因, never run_error."""
    agent = PhoneAgent(agent_config=AgentConfig(max_steps=3, device_id="device-1"))
    agent._graph = _RaisingGraph(_takeover_interrupt())

    result = agent.run_structured("登录测试任务")

    assert result.success is False
    assert result.finished is True
    assert result.error is None
    assert result.failure_cause == "takeover"
    assert result.final_message == "需要登录或验证码"
    assert result.hitl_count == 1
    assert result.steps == 0


def test_run_structured_interrupt_is_not_a_run_error() -> None:
    """The generic except path must not swallow the interrupt as an error."""
    agent = PhoneAgent(agent_config=AgentConfig(max_steps=3))
    agent._graph = _RaisingGraph(_takeover_interrupt("结构性无法完成"))

    result = agent.run_structured("任务")

    assert result.error is None
    assert result.failure_cause == "takeover"
    assert "Error" not in result.final_message


def test_eval_run_agent_task_handles_interrupt_defensively(monkeypatch) -> None:
    """Even if the interrupt escapes run_structured, the eval harness attributes
    it cleanly instead of crashing or recording run_error."""
    task = run_eval_module.EvalTask(id="t1", task="登录", category="hitl", max_steps=3)
    args = run_eval_module.parse_args.__wrapped__ if hasattr(
        run_eval_module.parse_args, "__wrapped__"
    ) else None

    def fake_agent_run(self, task_text):
        raise _takeover_interrupt("验证码")

    monkeypatch.setattr(PhoneAgent, "run_structured", fake_agent_run)
    monkeypatch.setattr("sys.argv", ["run_eval.py", "--dry-run"])

    result = run_eval_module.run_agent_task(task, run_eval_module.parse_args())

    assert result.success is False
    assert result.error is None
    assert result.failure_cause == "takeover"
    assert result.final_message == "验证码"


def test_result_record_carries_continuation_and_finish_source(
    monkeypatch, tmp_path
) -> None:
    """F2.5: continuation/locate/finish_source metrics flow into eval records."""
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(
        json.dumps([{"id": "t1", "task": "finish", "category": "smoke", "max_steps": 3}]),
        encoding="utf-8",
    )

    def fake_run_agent_task(task, args):
        return RunResult(
            success=False,
            finished=True,
            steps=30,
            failure_cause="goal_not_satisfied",
            finish_source="absolute_budget_exhausted",
            continuation_count=2,
            locate_count=1,
        )

    monkeypatch.setattr(run_eval_module, "run_agent_task", fake_run_agent_task)
    monkeypatch.setattr("sys.argv", ["run_eval.py", "--tasks", str(tasks_file)])

    output = run_eval_module.run_eval(run_eval_module.parse_args())

    assert output["summary"]["continuation_count"] == 2
    assert output["summary"]["locate_count"] == 1
    assert output["summary"]["finish_source_counts"] == {
        "absolute_budget_exhausted": 1
    }
    assert output["results"][0]["continuation_count"] == 2
    assert output["results"][0]["finish_source"] == "absolute_budget_exhausted"


def test_run_result_defaults_include_new_metrics() -> None:
    result = RunResult()
    assert result.locate_count == 0
    assert result.continuation_count == 0
    assert result.finish_source is None
