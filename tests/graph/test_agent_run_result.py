from dataclasses import dataclass

from phone_agent.agent import AgentConfig, PhoneAgent, RunResult


@dataclass
class FakeScreenshot:
    width: int = 1080
    height: int = 2400
    base64_data: str = "fake"


class FakeDeviceFactory:
    def get_screenshot(self, device_id=None):
        return FakeScreenshot()


class FakeGraph:
    def __init__(self, state):
        self.state = state
        self.initial_state = None
        self.config = None

    def invoke(self, initial_state, config):
        self.initial_state = initial_state
        self.config = config
        return self.state


def make_agent(final_state) -> PhoneAgent:
    agent = PhoneAgent(agent_config=AgentConfig(max_steps=3, device_id="device-1"))
    agent._graph = FakeGraph(final_state)
    return agent


def test_run_result_defaults_and_serialization() -> None:
    result = RunResult()

    assert result.to_dict() == {
        "success": False,
        "finished": False,
        "steps": 0,
        "duration": 0.0,
        "final_message": "",
        "error": None,
        "hitl_count": 0,
        "trace_id": "",
        "trace_path": None,
        "failure_cause": None,
        "retry_count": 0,
        "context_mode": "observe",
        "context_block_chars": 0,
        "context_truncated": False,
        "failure_memory_hit_count": 0,
        "repeated_failure_count": 0,
    }


def test_run_structured_returns_metrics_and_keeps_config(monkeypatch) -> None:
    final_state = {
        "finished": True,
        "step_count": 2,
        "action_result": {"success": True, "message": "done"},
        "error": None,
        "hitl_count": 1,
        "failure_cause": "wrong_page",
        "retry_count": 2,
        "context_mode": "inject",
        "context_block_chars": 42,
        "context_truncated": True,
        "failure_memory_hit_count": 1,
        "repeated_failure_count": 1,
    }
    agent = make_agent(final_state)
    monkeypatch.setattr(
        "phone_agent.agent.get_device_factory", lambda: FakeDeviceFactory()
    )

    result = agent.run_structured("task")

    assert result.success is True
    assert result.finished is True
    assert result.steps == 2
    assert result.final_message == "done"
    assert result.error is None
    assert result.hitl_count == 1
    assert result.trace_id
    assert result.trace_path
    assert result.failure_cause == "wrong_page"
    assert result.retry_count == 2
    assert result.context_mode == "inject"
    assert result.context_block_chars == 42
    assert result.context_truncated is True
    assert result.failure_memory_hit_count == 1
    assert result.repeated_failure_count == 1
    assert agent._graph.initial_state["hitl_count"] == 0
    assert agent._graph.initial_state["context_mode"] == "observe"
    assert agent._graph.config["configurable"]["trace_id"] == result.trace_id
    assert agent._graph.config["configurable"]["context_mode"] == "observe"
    assert agent._graph.config["configurable"]["trace_writer"] is not None


def test_run_keeps_string_compatibility(monkeypatch) -> None:
    final_state = {
        "finished": True,
        "step_count": 1,
        "action_result": {"success": True, "message": "done"},
        "error": None,
        "hitl_count": 0,
    }
    agent = make_agent(final_state)
    monkeypatch.setattr(
        "phone_agent.agent.get_device_factory", lambda: FakeDeviceFactory()
    )

    assert agent.run("task") == "done"


def test_run_structured_error_path_has_finished_semantics(monkeypatch) -> None:
    class BrokenDeviceFactory:
        def get_screenshot(self, device_id=None):
            raise RuntimeError("device unavailable")

    agent = make_agent({})
    monkeypatch.setattr(
        "phone_agent.agent.get_device_factory", lambda: BrokenDeviceFactory()
    )

    result = agent.run_structured("task")

    assert result.success is False
    assert result.finished is True
    assert result.error == "device unavailable"
    assert result.final_message == "Error: device unavailable"
    assert result.trace_path
