"""HITL resume: checkpointer-backed interrupt -> resume -> continue.

Deterministic tests only — no model mocks. The graph stub used by the
``run_live`` tests is an infrastructure stub (a fake graph object), not a
model output stub: it never presets a "model decision" for assertion purposes,
it only shapes interrupt/resume plumbing.
"""

import json
import sys

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END
from langgraph.types import Interrupt, Command, interrupt
from typing import TypedDict

from phone_agent.agent import (
    AgentConfig,
    PhoneAgent,
    RunResult,
    extract_interrupt,
    interrupt_payload,
)
from phone_agent.checkpoint import build_hitl_checkpointer


class _FakeDevice:
    width = 1080
    height = 2400


def _fake_device_factory(monkeypatch, device=None) -> None:
    import phone_agent.agent as agent_module

    factory = type("FakeFactory", (), {"get_screenshot": lambda self, device_id=None, timeout=10: device or _FakeDevice()})()
    monkeypatch.setattr(agent_module, "get_device_factory", lambda: factory)


def _final_state(**overrides) -> dict:
    state = {
        "step_count": 4,
        "finished": True,
        "error": None,
        "action_result": {"success": True, "message": "done"},
        "hitl_count": 1,
        "failure_cause": None,
    }
    state.update(overrides)
    return state


class _MarkerThenFinalGraph:
    """Infrastructure stub: first invoke returns an interrupt marker in the
    result (langgraph >=1.x behavior), resume invoke returns the final state."""

    def __init__(self, marker: list, final_state: dict) -> None:
        self.marker = marker
        self.final_state = final_state
        self.resumes: list = []
        self.configs: list = []

    def invoke(self, input, config):
        self.configs.append(config)
        if isinstance(input, Command):
            self.resumes.append(input.resume)
            return self.final_state
        return {"step_count": 2, "__interrupt__": self.marker}


class _RaisingGraph:
    """Infrastructure stub: the defensive GraphInterrupt path."""

    def __init__(self, interrupt: GraphInterrupt) -> None:
        self.interrupt = interrupt

    def invoke(self, initial_state, config):
        raise self.interrupt


def _takeover_marker(message: str = "请登录") -> list:
    return [Interrupt(value={"type": "takeover", "message": message})]


# ----------------------------------------------------------------------
# langgraph API verification (minimal real graph, no model)
# ----------------------------------------------------------------------


def test_minimal_real_graph_interrupt_resume_preserves_state() -> None:
    """A real StateGraph interrupt + InMemorySaver resumes via Command(resume)
    and the state accumulated before the interrupt survives."""
    class S(TypedDict):
        value: int
        answer: str
        marker: str

    def step1(state: S) -> dict:
        reply = interrupt({"type": "takeover", "message": "login"})
        return {"answer": reply, "value": state["value"] + 1}

    def step2(state: S) -> dict:
        return {"marker": "done", "value": state["value"] + 1}

    g = StateGraph(S)
    g.add_node("step1", step1)
    g.add_node("step2", step2)
    g.add_edge(START, "step1")
    g.add_edge("step1", "step2")
    g.add_edge("step2", END)
    app = g.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "resume-t1"}}

    out = app.invoke({"value": 1, "answer": "", "marker": ""}, config)
    assert "__interrupt__" in out
    marker = out["__interrupt__"]
    assert marker[0].value["type"] == "takeover"

    final = app.invoke(Command(resume="ok"), config)
    assert final["value"] == 3  # 1 -> step1 -> step2
    assert final["answer"] == "ok"
    assert final["marker"] == "done"


def test_minimal_real_graph_interrupt_returns_marker_not_exception() -> None:
    """On langgraph >= 1.x a checkpointer-backed interrupt surfaces as a
    __interrupt__ marker in the invoke result, not as GraphInterrupt."""
    class S(TypedDict):
        value: int

    def node(state: S) -> dict:
        interrupt({"type": "confirmation", "message": "pay?"})
        return {"value": state["value"] + 1}

    g = StateGraph(S)
    g.add_node("node", node)
    g.add_edge(START, "node")
    g.add_edge("node", END)
    app = g.compile(checkpointer=InMemorySaver())
    out = app.invoke({"value": 0}, {"configurable": {"thread_id": "resume-t2"}})
    assert "__interrupt__" in out
    assert out["__interrupt__"][0].value["type"] == "confirmation"


def test_checkpointer_factory_returns_plain_in_memory_saver() -> None:
    """The HITL saver seam returns a plain InMemorySaver (documented: not
    wrapped in RedactingSerializer because stub-at-egress breaks resume)."""
    saver = build_hitl_checkpointer()
    assert isinstance(saver, InMemorySaver)


def test_redacting_serializer_stubs_checkpoint_metadata_versions() -> None:
    """Pins the reason RedactingSerializer cannot serve as the MemorySaver
    serde: it stubs structural channel_versions strings at egress, which the
    saver later uses as blob keys (unhashable on resume)."""
    from phone_agent.checkpoint.serde import RedactingSerializer

    class _Inner:
        def dumps_typed(self, value):
            return ("json", json.dumps(value).encode("utf-8"))

        def loads_typed(self, payload):
            return json.loads(payload[1].decode("utf-8"))

    serde = RedactingSerializer(inner=_Inner())
    checkpoint = {
        "channel_versions": {"__start__": "00000000000000000000000000000002.0.1"},
        "channel_values": {"task": "登录并支付"},
    }
    _type, blob = serde.dumps_typed(checkpoint)
    restored = json.loads(blob.decode("utf-8"))
    # channel_versions string is stubbed -> would become an unhashable dict key
    # in InMemorySaver._load_blobs -> resume raises TypeError.
    assert isinstance(restored["channel_versions"]["__start__"], dict)
    assert restored["channel_versions"]["__start__"]["redacted"] is True
    assert restored["channel_values"]["task"]["redacted"] is True


# ----------------------------------------------------------------------
# extract_interrupt helper
# ----------------------------------------------------------------------


def test_extract_interrupt_reads_marker_and_ignores_normal_result() -> None:
    result = {"step_count": 2, "__interrupt__": _takeover_marker("请登录")}
    assert extract_interrupt(result) == ("请登录", "takeover", None)

    result2 = {"step_count": 2, "__interrupt__": [Interrupt(value="plain string")]}
    assert extract_interrupt(result2) is None

    assert extract_interrupt({"step_count": 2}) is None
    assert extract_interrupt(None) is None


def test_extract_interrupt_carries_payload_prompt() -> None:
    """The confirmation/goal-approval payloads carry their own prompt; it is
    surfaced so run_live can show node-authored text instead of the generic
    takeover prompt (F3)."""
    marker = [
        Interrupt(
            value={
                "type": "confirmation",
                "message": "敏感操作",
                "prompt": "Sensitive operation: 敏感操作\nConfirm? (Y/N): ",
            }
        )
    ]
    assert extract_interrupt({"__interrupt__": marker}) == (
        "敏感操作",
        "confirmation",
        "Sensitive operation: 敏感操作\nConfirm? (Y/N): ",
    )


def test_interrupt_payload_extracts_from_exception() -> None:
    exc = GraphInterrupt(interrupts=(Interrupt(value={"type": "takeover", "message": "验证码"}),))
    assert interrupt_payload(exc) == ("验证码", "takeover")


# ----------------------------------------------------------------------
# run_live loop (stubbed graph object — infrastructure, not model)
# ----------------------------------------------------------------------


def test_run_live_resumes_after_marker_interrupt(monkeypatch, tmp_path) -> None:
    _fake_device_factory(monkeypatch)
    prompts: list[str] = []
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True,
            trace_dir=str(tmp_path),
            max_steps=3,
            device_id="device-1",
        )
    )
    agent._graph = _MarkerThenFinalGraph(
        _takeover_marker("请登录"), _final_state(hitl_count=1)
    )

    result = agent.run_live(
        "登录测试任务", resume_input=lambda prompt: prompts.append(prompt) or "Y"
    )

    assert result.success is True
    assert result.finished is True
    assert result.steps == 4
    assert result.hitl_count == 1
    assert result.error is None
    assert len(prompts) == 1
    assert "请登录" in prompts[0]
    assert "输入 n" in prompts[0]
    assert agent._graph.resumes == ["Y"]

    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert events[:4] == ["run_start", "run_interrupted", "run_resumed", "run_end"]


def test_run_live_abort_on_n_returns_terminal_attribution(
    monkeypatch, tmp_path
) -> None:
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _MarkerThenFinalGraph(_takeover_marker("需要登录或验证码"), _final_state())

    result = agent.run_live("任务", resume_input=lambda prompt: "n")

    assert result.success is False
    assert result.finished is True
    assert result.error is None
    assert result.failure_cause == "takeover"
    assert result.final_message == "需要登录或验证码"
    assert result.hitl_count == 1
    assert result.steps == 2  # step_count from the interrupted result
    assert agent._graph.resumes == []
    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert "run_interrupted" in events
    assert "run_resumed" not in events


def test_run_live_multiple_interrupts_accumulate_hitl_count(
    monkeypatch, tmp_path
) -> None:
    _fake_device_factory(monkeypatch)
    answers = iter(["Y", "Y"])
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )

    class _TwoMarkersThenFinal:
        def __init__(self) -> None:
            self.calls = 0
            self.resumes = []

        def invoke(self, input, config):
            if isinstance(input, Command):
                self.resumes.append(input.resume)
                self.calls += 1
                if self.calls >= 2:
                    return _final_state(hitl_count=2)
            return {"step_count": 1, "__interrupt__": _takeover_marker("第二次")}

    graph = _TwoMarkersThenFinal()
    agent._graph = graph

    result = agent.run_live("任务", resume_input=lambda prompt: next(answers))

    assert result.success is True
    assert result.hitl_count == 2
    assert graph.resumes == ["Y", "Y"]
    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert events.count("run_interrupted") == 2
    assert events.count("run_resumed") == 2


def _confirmation_marker(message: str = "敏感操作") -> list:
    return [
        Interrupt(
            value={
                "type": "confirmation",
                "message": message,
                "prompt": f"Sensitive operation: {message}\nConfirm? (Y/N): ",
            }
        )
    ]


def test_run_live_confirmation_empty_enter_is_fail_closed_reject(
    monkeypatch, tmp_path
) -> None:
    """F3: an empty Enter on a confirmation interrupt resumes as "N" (the
    node then records finished=True and the graph terminates cleanly) — never
    the old takeover-style "Enter continues". The payload's own prompt is
    shown."""
    _fake_device_factory(monkeypatch)
    prompts: list[str] = []
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _MarkerThenFinalGraph(
        _confirmation_marker(),
        _final_state(
            hitl_count=1,
            finished=True,
            action_result={
                "success": False,
                "should_finish": True,
                "message": "User cancelled sensitive operation",
            },
        ),
    )

    result = agent.run_live(
        "任务", resume_input=lambda prompt: prompts.append(prompt) or ""
    )

    assert agent._graph.resumes == ["N"]
    assert "Confirm? (Y/N)" in prompts[0]
    assert result.finished is True
    assert result.success is False  # node cancelled -> finished without success
    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert "run_interrupted" in events
    assert "run_resumed" in events
    assert "run_end" in events


def test_run_live_confirmation_yes_resumes_y(monkeypatch, tmp_path) -> None:
    """F3: an explicit y/yes on a confirmation interrupt resumes as "Y" so the
    pending action dispatches."""
    _fake_device_factory(monkeypatch)
    prompts: list[str] = []
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _MarkerThenFinalGraph(
        _confirmation_marker(), _final_state(hitl_count=1, success=True)
    )

    result = agent.run_live(
        "任务", resume_input=lambda prompt: prompts.append(prompt) or "y"
    )

    assert agent._graph.resumes == ["Y"]
    assert "Confirm? (Y/N)" in prompts[0]
    assert result.success is True


def test_run_live_confirmation_without_payload_prompt_uses_fallback(
    monkeypatch, tmp_path
) -> None:
    """F3 regression: a confirmation payload without a ``prompt`` field must
    fall back to the assembled Y/N prompt (and must not crash — the fallback
    reads lang from the initial state, not from an undefined local)."""
    _fake_device_factory(monkeypatch)
    prompts: list[str] = []
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3,
            lang="cn",
        )
    )
    marker = [Interrupt(value={"type": "confirmation", "message": "敏感操作"})]
    agent._graph = _MarkerThenFinalGraph(
        marker, _final_state(hitl_count=1, finished=True)
    )

    result = agent.run_live(
        "任务", resume_input=lambda prompt: prompts.append(prompt) or ""
    )

    assert agent._graph.resumes == ["N"]
    assert "确认操作？(Y/N)" in prompts[0]
    assert result.error is None  # the fallback branch did not raise


def test_run_live_confirmation_n_resumes_n_not_abort(
    monkeypatch, tmp_path
) -> None:
    """F3: "n" on a confirmation resumes as "N" (fail-closed, graph
    terminates itself) instead of the takeover-style early return — the
    graph stub never sees an early return and the resume list stays exact."""
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _MarkerThenFinalGraph(
        _confirmation_marker(), _final_state(hitl_count=1, finished=True)
    )

    result = agent.run_live("任务", resume_input=lambda prompt: "n")

    assert agent._graph.resumes == ["N"]
    assert result.finished is True


def test_run_live_goal_approval_resumes_y_or_n(monkeypatch, tmp_path) -> None:
    """F3: goal_approval shows the payload approval prompt and maps y -> "Y"
    (approve), anything else (empty Enter included) -> "N" (reject, the node
    falls back to the heuristic weak contract and continues)."""
    _fake_device_factory(monkeypatch)
    prompts: list[str] = []
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    marker = [
        Interrupt(
            value={
                "type": "goal_approval",
                "goal_contract": {"redacted_objective": "task"},
                "prompt": "Approve the goal contract? (Y/N/Edit): ",
            }
        )
    ]
    agent._graph = _MarkerThenFinalGraph(marker, _final_state(hitl_count=0))

    yes = agent.run_live(
        "任务", resume_input=lambda prompt: prompts.append(prompt) or "y"
    )
    assert agent._graph.resumes == ["Y"]
    assert "Approve the goal contract" in prompts[0]

    no = agent.run_live(
        "任务", resume_input=lambda prompt: prompts.append(prompt) or ""
    )
    assert agent._graph.resumes == ["Y", "N"]


def test_run_live_defensive_graph_interrupt_exception_path(
    monkeypatch, tmp_path
) -> None:
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _RaisingGraph(
        GraphInterrupt(
            interrupts=(Interrupt(value={"type": "takeover", "message": "结构性无法完成"}),)
        )
    )

    result = agent.run_live("任务", resume_input=lambda prompt: "Y")

    assert result.success is False
    assert result.finished is True
    assert result.failure_cause == "takeover"
    assert result.final_message == "结构性无法完成"
    assert result.hitl_count == 1


def test_run_live_requires_enable_hitl_resume(monkeypatch) -> None:
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(agent_config=AgentConfig(max_steps=3))

    with pytest.raises(ValueError, match="enable_hitl_resume"):
        agent.run_live("任务")


def test_run_live_error_path_returns_run_error(monkeypatch, tmp_path) -> None:
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )

    class _BoomGraph:
        def invoke(self, initial_state, config):
            raise RuntimeError("adb exploded")

    agent._graph = _BoomGraph()

    result = agent.run_live("任务", resume_input=lambda prompt: "Y")

    assert result.error == "adb exploded"
    assert result.success is False
    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert "run_error" in events


# ----------------------------------------------------------------------
# config plumbing
# ----------------------------------------------------------------------


def test_agent_config_enable_hitl_resume_defaults_false() -> None:
    assert AgentConfig().enable_hitl_resume is False


def test_graph_config_thread_id_only_when_resume_enabled(monkeypatch) -> None:
    _fake_device_factory(monkeypatch)
    agent_off = PhoneAgent(agent_config=AgentConfig(max_steps=3))
    config_off = agent_off._build_graph_config(_FakeDevice(), "trace-1")
    assert "thread_id" not in config_off["configurable"]

    agent_on = PhoneAgent(
        agent_config=AgentConfig(enable_hitl_resume=True, max_steps=3)
    )
    config_on = agent_on._build_graph_config(_FakeDevice(), "trace-1")
    assert config_on["configurable"]["thread_id"] == "trace-1"


def test_phone_agent_compiles_graph_with_checkpointer_when_enabled() -> None:
    agent_on = PhoneAgent(
        agent_config=AgentConfig(enable_hitl_resume=True, max_steps=3)
    )
    assert agent_on._checkpointer is not None
    assert agent_on._graph.checkpointer is agent_on._checkpointer

    agent_off = PhoneAgent(agent_config=AgentConfig(max_steps=3))
    assert agent_off._checkpointer is None
    assert agent_off._graph.checkpointer is None


# ----------------------------------------------------------------------
# batch semantics regression (run_structured: real compiled mini-graph)
# ----------------------------------------------------------------------


def _interrupt_mini_graph(
    message: str = "need human",
    type_: str = "takeover",
    prompt: str | None = None,
):
    """A real compiled StateGraph whose single node interrupts immediately.

    No checkpointer: on langgraph >= 1.x ``invoke`` returns
    ``{'x': 0, '__interrupt__': [Interrupt(...)]}`` instead of raising
    GraphInterrupt — the exact real-flight shape that made the old
    ``_RaisingGraph`` stubs self-deceiving (the exception path they protected
    never fires).
    """

    class S(TypedDict, total=False):
        x: int

    payload: dict = {"message": message, "type": type_}
    if prompt:
        payload["prompt"] = prompt

    def node(state: S) -> dict:
        interrupt(payload)
        return {"x": 1}

    b = StateGraph(S)
    b.add_node("n", node)
    b.set_entry_point("n")
    return b.compile()


def test_run_structured_semantics_unchanged_with_flag_off(monkeypatch) -> None:
    """The structured path attributes a marker-returning HITL interrupt as a
    clean terminal (takeover), not as a max-steps run — driven by a real
    compiled mini-graph, not a fake graph that raises."""
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(agent_config=AgentConfig(max_steps=3, device_id="device-1"))
    agent._graph = _interrupt_mini_graph("需要登录或验证码")

    result = agent.run_structured("登录测试任务")

    assert result.success is False
    assert result.finished is True
    assert result.error is None
    assert result.failure_cause == "takeover"
    assert result.final_message == "需要登录或验证码"
    assert result.hitl_count == 1
    assert result.steps == 0  # the mini-graph has no step_count channel


def test_run_structured_semantics_unchanged_with_flag_on(monkeypatch) -> None:
    """Even with enable_hitl_resume=True the structured path is not the live
    loop: a marker-returning interrupt still terminates with the batch
    attribution."""
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(enable_hitl_resume=True, max_steps=3)
    )
    agent._graph = _interrupt_mini_graph("验证码")

    result = agent.run_structured("登录")

    assert result.failure_cause == "takeover"
    assert result.hitl_count == 1
    assert result.final_message == "验证码"


def test_run_structured_interrupt_emits_run_interrupted_trace(
    monkeypatch, tmp_path
) -> None:
    """The marker path emits the run_interrupted trace event (the pre-fix
    batch path fell through to run_end with a misleading max-steps message and
    no interrupt trace at all)."""
    _fake_device_factory(monkeypatch)
    agent = PhoneAgent(
        agent_config=AgentConfig(
            enable_hitl_resume=True, trace_dir=str(tmp_path), max_steps=3
        )
    )
    agent._graph = _interrupt_mini_graph(
        "需要登录", type_="confirmation", prompt="Confirm? (Y/N): "
    )

    result = agent.run_structured("登录")

    assert result.failure_cause == "confirmation"
    events = [
        json.loads(line)["event"]
        for line in open(result.trace_path, encoding="utf-8")
    ]
    assert "run_interrupted" in events
    assert "run_error" not in events
    interrupted = next(
        json.loads(line)["payload"]
        for line in open(result.trace_path, encoding="utf-8")
        if json.loads(line)["event"] == "run_interrupted"
    )
    assert interrupted["type"] == "confirmation"
    # P0 #10: the trace redacts the message text at egress — only the stable
    # type attribution is asserted.
    assert "message" in interrupted


# ----------------------------------------------------------------------
# run_diagnosis live wiring
# ----------------------------------------------------------------------


def test_run_diagnosis_live_path_uses_run_live_with_checkpointer(
    monkeypatch, tmp_path
) -> None:
    """The run_diagnosis --live branch drives PhoneAgent.run_live with
    enable_hitl_resume=True and shapes the result into the eval record format.
    The agent is an infrastructure stub (no model)."""
    import importlib.util
    from pathlib import Path

    import phone_agent.agent as agent_module

    script = (
        Path(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "phone-agent-live-diagnosis"
        / "scripts"
        / "run_diagnosis.py"
    )
    spec = importlib.util.spec_from_file_location("run_diagnosis_module", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_diagnosis_module"] = mod
    spec.loader.exec_module(mod)

    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs) -> None:
            captured["model_config"] = kwargs.get("model_config")
            captured["agent_config"] = kwargs.get("agent_config")

        def run_live(self, task, resume_input=None):
            captured["task"] = task
            captured["resume_input"] = resume_input
            return RunResult(
                success=True,
                finished=True,
                steps=3,
                hitl_count=2,
                trace_id="live-1",
                final_message="done",
            )

        def unload_models(self) -> None:
            captured["unloaded"] = True

    monkeypatch.setattr(agent_module, "PhoneAgent", _FakeAgent)
    monkeypatch.setattr(
        "sys.argv",
        ["run_diagnosis.py", "--live", "登录并支付", "--output-dir", str(tmp_path)],
    )

    args = mod.parse_args()
    run_dir = tmp_path / "live-run"
    run_dir.mkdir()
    command_result, eval_result = mod.run_live_agent(
        args, run_dir=run_dir, trace_dir=tmp_path / "traces"
    )

    assert captured["task"] == "登录并支付"
    assert captured["resume_input"] is input
    assert captured["agent_config"].enable_hitl_resume is True
    assert captured["unloaded"] is True
    assert eval_result["results"][0]["hitl_count"] == 2
    assert eval_result["summary"]["success"] == 1
    assert command_result.returncode == 0
    assert (run_dir / "run_output.log").exists()
    assert (run_dir / "status.json").exists()


def test_run_diagnosis_live_dry_run_conflict_checked_before_device_ops(
    monkeypatch, tmp_path
) -> None:
    """F11: --live --dry-run bails with code 2 BEFORE any device operation —
    collect_preflight and reset_app_on_device (a real `adb shell pm clear`)
    must never run for the conflicting flag combination."""
    import importlib.util
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "phone-agent-live-diagnosis"
        / "scripts"
        / "run_diagnosis.py"
    )
    spec = importlib.util.spec_from_file_location("run_diagnosis_module", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_diagnosis_module"] = mod
    spec.loader.exec_module(mod)

    preflight_calls = {"count": 0}
    reset_calls = {"count": 0}
    original_preflight = mod.collect_preflight
    original_reset = mod.reset_app_on_device

    def _counting_preflight(args):
        preflight_calls["count"] += 1
        return original_preflight(args)

    def _counting_reset(args):
        reset_calls["count"] += 1
        return original_reset(args)

    monkeypatch.setattr(mod, "collect_preflight", _counting_preflight)
    monkeypatch.setattr(mod, "reset_app_on_device", _counting_reset)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_diagnosis.py",
            "--live",
            "登录并支付",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "conflict"),
        ],
    )

    code = mod.main()

    assert code == 2
    assert preflight_calls["count"] == 0
    assert reset_calls["count"] == 0
    assert not (tmp_path / "conflict").exists()
