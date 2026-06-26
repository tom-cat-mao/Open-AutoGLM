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
        "context_mode": "inject",
        "context_strategy": "unknown",
        "prompt_version": "context_harness_v1",
        "selected_sections": [],
        "context_block_chars": 0,
        "context_truncated": False,
        "messages_before": 0,
        "messages_after": 0,
        "message_chars_before": 0,
        "message_chars_after": 0,
        "approx_tokens_before": 0,
        "approx_tokens_after": 0,
        "failure_memory_hit_count": 0,
        "repeated_failure_count": 0,
        "verifier_status": None,
        "verifier_failure_cause": None,
        "verifier_evidence": None,
        "grounding_provider": None,
        "grounding_latency_ms": None,
        "grounding_failure_code": None,
        "grounding_screen_hash": None,
        "grounding_candidate_count": 0,
        "selected_grounding_candidate_id": None,
        "error_layer": None,
        "error_code": None,
        "recoverable": None,
        "retry_policy": None,
    }


def test_agent_config_defaults_to_inject_context_and_hybrid_grounding() -> None:
    config = AgentConfig()

    assert config.context_mode == "inject"
    assert config.grounding_provider_name == "hybrid"


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
        "context_strategy": "inject_redacted_block",
        "prompt_version": "context_harness_v1",
        "selected_sections": ["screen_belief"],
        "context_block_chars": 42,
        "context_truncated": True,
        "messages_before": 4,
        "messages_after": 4,
        "message_chars_before": 1000,
        "message_chars_after": 800,
        "approx_tokens_before": 250,
        "approx_tokens_after": 200,
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
    assert result.context_strategy == "inject_redacted_block"
    assert result.prompt_version == "context_harness_v1"
    assert result.selected_sections == ["screen_belief"]
    assert result.context_block_chars == 42
    assert result.context_truncated is True
    assert result.messages_before == 4
    assert result.messages_after == 4
    assert result.message_chars_after == 800
    assert result.failure_memory_hit_count == 1
    assert result.repeated_failure_count == 1
    assert agent._graph.initial_state["hitl_count"] == 0
    assert agent._graph.initial_state["context_mode"] == "inject"
    assert agent._graph.config["configurable"]["trace_id"] == result.trace_id
    assert agent._graph.config["configurable"]["context_mode"] == "inject"
    assert agent._graph.config["configurable"]["prompt_version"] == "context_harness_v1"
    assert agent._graph.config["configurable"]["trace_writer"] is not None


def test_cli_default_output_mode_is_structured(monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    main_path = Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("phone_agent_cli_main_default", main_path)
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    monkeypatch.delenv("PHONE_AGENT_OUTPUT_MODE", raising=False)
    monkeypatch.setattr("sys.argv", ["main.py"])

    assert main.parse_args().output_mode == "json_schema"


def test_cli_defaults_context_and_grounding(monkeypatch) -> None:
    import importlib.util
    from pathlib import Path

    main_path = Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("phone_agent_cli_main_defaults", main_path)
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    monkeypatch.delenv("PHONE_AGENT_CONTEXT_MODE", raising=False)
    monkeypatch.delenv("PHONE_AGENT_GROUNDING_PROVIDER", raising=False)
    monkeypatch.setattr("sys.argv", ["main.py"])

    args = main.parse_args()
    assert args.context_mode == "inject"
    assert args.grounding_provider == "hybrid"


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


def test_agent_config_passes_remote_grounding_options(monkeypatch) -> None:
    final_state = {"finished": True, "step_count": 1, "action_result": {"success": True, "message": "done"}, "error": None, "hitl_count": 0}
    agent = PhoneAgent(
        agent_config=AgentConfig(
            max_steps=3,
            device_id="device-1",
            grounding_provider_name="hybrid_remote",
            remote_grounding_base_url="https://api.stepfun.com/v1",
            remote_grounding_api_key_env="STEPFUN_TEST_KEY",
            remote_grounding_model="step-3.7-flash",
            remote_grounding_max_size=720,
            remote_grounding_timeout=12,
            remote_grounding_allow_raw_hints=True,
        )
    )
    agent._graph = FakeGraph(final_state)
    monkeypatch.setattr("phone_agent.agent.get_device_factory", lambda: FakeDeviceFactory())

    result = agent.run_structured("task")
    cfg = agent._graph.config["configurable"]

    assert result.success is True
    assert cfg["grounding_provider_name"] == "hybrid_remote"
    assert cfg["remote_grounding_base_url"] == "https://api.stepfun.com/v1"
    assert cfg["remote_grounding_api_key_env"] == "STEPFUN_TEST_KEY"
    assert cfg["remote_grounding_model"] == "step-3.7-flash"
    assert cfg["remote_grounding_max_size"] == 720
    assert cfg["remote_grounding_timeout"] == 12
    assert cfg["remote_grounding_allow_raw_hints"] is True
