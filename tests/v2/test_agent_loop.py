"""End-to-end thin-loop test: read_screen -> tap -> finish with all fakes.

Per refactor-thin-loop-v2 §12. Uses a scripted fake chat model, a duck-typed
fake session, and fake tools -- no real device, MLX, or network. The concurrent
W-core / W-tools modules (``phone_agent.v2.model/session/tools/prompts``) are
not present in this worktree, so they are injected into ``sys.modules`` as
lightweight fakes before the agent is assembled.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool


# --------------------------------------------------------------------------
# Fake chat model that supports bind_tools and replays scripted AI messages.
# --------------------------------------------------------------------------
class ScriptedToolModel(BaseChatModel):
    """Replays a fixed list of AIMessages, one per model call."""

    responses: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        response = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        # Tool schemas are irrelevant to a scripted model; keep binding a no-op.
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"


def _ai_tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


# --------------------------------------------------------------------------
# Fake session (duck-typed against §6 PhoneSession).
# --------------------------------------------------------------------------
@dataclass
class FakeObservation:
    screenshot_b64: str = "QUJD"  # "ABC"
    width: int = 1080
    height: int = 2400
    current_app: str = "com.android.settings"
    screen_seq: int = 0
    marks: dict = field(default_factory=dict)


@dataclass
class FakeSession:
    config: Any = None
    marks: dict = field(default_factory=dict)
    screen_seq: int = 0
    finished: bool = False
    finish_summary: str | None = None
    takeover_reason: str | None = None
    taps: list = field(default_factory=list)

    def observe(self) -> FakeObservation:
        self.screen_seq += 1
        return FakeObservation(screen_seq=self.screen_seq, current_app="com.android.settings")


# --------------------------------------------------------------------------
# Fake tools (§7 semantics, minimal).
# --------------------------------------------------------------------------
def _build_fake_tools(session: FakeSession):
    @tool
    def read_screen() -> str:
        """Re-observe the current screen and return an observation digest."""
        obs = session.observe()
        return f"[OBS] app={obs.current_app} screen#{obs.screen_seq}"

    @tool
    def tap(target_mark_id: str | None = None, target_description: str | None = None) -> str:
        """Tap a UI element by mark id or natural-language description."""
        session.taps.append(target_mark_id or target_description)
        return "OK. tapped"

    @tool
    def finish(summary: str, evidence: list[str]) -> str:
        """Declare the task finished. evidence must be non-empty."""
        if not evidence:
            return "error: evidence must be non-empty"
        session.finished = True
        session.finish_summary = summary
        return "已记录完成声明"

    return [read_screen, tap, finish]


# --------------------------------------------------------------------------
# Fixture: inject fake v2 modules and hand back a configured agent.
# --------------------------------------------------------------------------
@dataclass
class FakeConfig:
    lang: str = "cn"
    max_model_calls: int = 20
    trace_dir: str = ".traces"
    trace_enabled: bool = False


@pytest.fixture
def scripted_agent(tmp_path, monkeypatch):
    session = FakeSession()

    responses = [
        _ai_tool_call("read_screen", {}, "c1"),
        _ai_tool_call("tap", {"target_mark_id": "ax_1"}, "c2"),
        _ai_tool_call("finish", {"summary": "打开了设置", "evidence": ["屏幕显示设置页"]}, "c3"),
        AIMessage(content="任务完成"),
    ]
    model = ScriptedToolModel(responses=responses)

    model_mod = types.ModuleType("phone_agent.v2.model")
    model_mod.build_chat_model = lambda config: model
    session_mod = types.ModuleType("phone_agent.v2.session")
    session_mod.PhoneSession = lambda config: session
    tools_mod = types.ModuleType("phone_agent.v2.tools")
    tools_mod.build_tools = lambda sess, config: _build_fake_tools(sess)
    prompts_mod = types.ModuleType("phone_agent.v2.prompts")
    prompts_mod.get_system_prompt = lambda lang="cn": "你是手机智能体。"

    for name, mod in [
        ("phone_agent.v2.model", model_mod),
        ("phone_agent.v2.session", session_mod),
        ("phone_agent.v2.tools", tools_mod),
        ("phone_agent.v2.prompts", prompts_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    from phone_agent.v2.agent import ThinPhoneAgent

    config = FakeConfig(trace_dir=str(tmp_path), trace_enabled=True)
    agent = ThinPhoneAgent(config)
    return agent, session


def test_first_observation_includes_marks_digest():
    """The opening user message must carry the marks digest (same [OBS] shape
    as the tool path) so the model can address ``target_mark_id`` from step 1
    instead of burning a read_screen."""

    from phone_agent.grounding.provider import MarkCandidate
    from phone_agent.v2.agent import _first_observation_content

    obs = SimpleNamespace(
        screenshot_b64="QUJD",
        screen_seq=1,
        current_app="com.android.settings",
        marks=[
            MarkCandidate(
                mark_id="ax_1@e1",
                bbox=[0, 100, 1080, 300],
                center=[500, 300],
                role="TextView",
                text_summary="WLAN",
                epoch=1,
            )
        ],
        mime_type="image/png",
    )
    content = _first_observation_content(obs, "打开WLAN")
    texts = [b["text"] for b in content if b.get("type") == "text"]
    assert texts[0] == "打开WLAN"
    obs_block = texts[1]
    assert "[OBS] app=com.android.settings screen#1" in obs_block
    assert "marks (1)" in obs_block
    assert "ax_1@e1" in obs_block
    assert "WLAN" in obs_block


def test_thin_loop_read_tap_finish(scripted_agent):
    agent, session = scripted_agent
    assert agent.session.usage_ledger is agent.usage_ledger
    result = agent.run("打开设置", hitl_handler=lambda prompt: "approve")

    assert result.success is True
    assert "打开了设置" in result.reason
    # tap landed on the mark the model selected.
    assert session.taps == ["ax_1"]
    assert session.finished is True
    # Trace file was produced.
    assert result.trace_path is not None
    from pathlib import Path

    assert Path(result.trace_path).exists()


def test_thin_loop_call_sequence_recorded(scripted_agent):
    import json
    from pathlib import Path

    agent, _ = scripted_agent
    agent.run("打开设置", hitl_handler=lambda prompt: "approve")

    events = [
        json.loads(line)
        for line in Path(agent.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    tool_names = [e["tool"] for e in events if e["event"] == "tool_call"]
    assert tool_names == ["read_screen", "tap", "finish"]
    assert any(e["event"] == "run_end" for e in events)


def test_fake_run_launches_static_unknown_alias_through_app_kb(tmp_path, monkeypatch):
    """Full fake loop: model term -> KB learning slot -> package -> device."""

    from phone_agent.config.apps import DEFAULT_APP_REGISTRY
    from phone_agent.v2.appkb import AppKnowledge, AppKnowledgeStore
    from tests.v2._doubles import FakeDeviceFactory, FakePhoneSession

    assert DEFAULT_APP_REGISTRY.resolve_term("哔哩哔哩").status == "unknown"

    store = AppKnowledgeStore(str(tmp_path / "memory"))
    timestamp = "2026-01-01T00:00:00+00:00"
    store.upsert(
        {
            "term": "哔哩哔哩",
            "label": "哔哩哔哩",
            "package": "tv.danmaku.bili",
            "kind": "alias",
            "scope": "global",
            "confidence": 0.9,
            "success_count": 1,
            "first_seen": timestamp,
            "last_seen": timestamp,
            "stale": False,
        }
    )
    device = FakeDeviceFactory(installed=frozenset({"tv.danmaku.bili"}))
    session = FakePhoneSession({}, device_factory=device)
    session.app_store = store
    session.app_knowledge = AppKnowledge(store, device_id="serial-1")

    model = ScriptedToolModel(
        responses=[
            _ai_tool_call(
                "launch_app",
                {"app_name": "哔哩哔哩", "intent": "打开视频应用"},
                "launch",
            ),
            _ai_tool_call(
                "finish",
                {
                    "summary": "已打开哔哩哔哩",
                    "evidence": ["launch_app 返回成功"],
                    "intent": "结束任务",
                },
                "finish",
            ),
        ]
    )
    model_mod = types.ModuleType("phone_agent.v2.model")
    model_mod.build_chat_model = lambda config: model
    session_mod = types.ModuleType("phone_agent.v2.session")
    session_mod.PhoneSession = lambda config: session
    prompts_mod = types.ModuleType("phone_agent.v2.prompts")
    prompts_mod.get_system_prompt = lambda lang="cn": "你是手机智能体。"
    for name, mod in [
        ("phone_agent.v2.model", model_mod),
        ("phone_agent.v2.session", session_mod),
        ("phone_agent.v2.prompts", prompts_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    from phone_agent.v2.agent import ThinPhoneAgent

    config = FakeConfig(trace_dir=str(tmp_path), trace_enabled=False)
    config.device_id = "serial-1"
    config.app_kb_enabled = True
    config.taskdoc_enabled = False
    config.finish_verify = "off"
    config.safety_mode = "off"
    agent = ThinPhoneAgent(config)

    result = agent.run("打开哔哩哔哩")

    assert result.success is True
    assert device.launched == ["哔哩哔哩"]


# --------------------------------------------------------------------------
# Terminal-reason resolution (S1 §3.2): numeric, no "limit"-string matching.
# Built via __new__ so we exercise the pure decision logic without the full
# create_agent / middleware assembly.
# --------------------------------------------------------------------------
def _bare_agent(*, steps: int, budget: int, finished=False, takeover=None, hitl_exhausted=False, token_exhausted=False):
    from phone_agent.v2.agent import ThinPhoneAgent

    agent = ThinPhoneAgent.__new__(ThinPhoneAgent)
    agent.config = FakeConfig(max_model_calls=budget)
    agent.trace_path = None
    agent._trace = SimpleNamespace(_step=steps)
    agent._budget = SimpleNamespace(exhausted=token_exhausted)
    agent.session = SimpleNamespace(
        finished=finished,
        finish_summary="做完了" if finished else None,
        takeover_reason=takeover,
    )
    agent._hitl_exhausted = hitl_exhausted
    return agent


def test_build_result_finished_is_success():
    result = _bare_agent(steps=3, budget=20, finished=True)._build_result({})
    assert result.success is True
    assert result.reason == "做完了"
    assert result.steps == 3


def test_build_result_takeover_takes_priority_over_budget():
    # Even with the budget spent, an explicit takeover wins the priority order.
    agent = _bare_agent(steps=20, budget=20, takeover="需要人工登录")
    result = agent._build_result({})
    assert result.success is False
    assert result.reason == "需要人工登录"


def test_build_result_budget_exhausted_is_loop_fuse():
    # steps >= fuse with no finish and no token-exhaust -> loop_fuse (A4 rename).
    result = _bare_agent(steps=100, budget=100)._build_result({})
    assert result.success is False
    assert result.reason == "loop_fuse"


def test_build_result_token_budget_exhausted_beats_fuse():
    # The token-cost ceiling fired: reason is token_budget_exhausted even though
    # steps are below the loop fuse.
    result = _bare_agent(steps=5, budget=100, token_exhausted=True)._build_result({})
    assert result.success is False
    assert result.reason == "token_budget_exhausted"


def test_build_result_model_stopped_below_budget():
    # Model stopped emitting tool calls before the fuse ran out.
    result = _bare_agent(steps=4, budget=100)._build_result({})
    assert result.success is False
    assert result.reason == "model_stopped"


def test_build_result_hitl_resume_exhausted_before_budget_check():
    # A spent HITL-resume budget is reported even though steps < model budget.
    agent = _bare_agent(steps=4, budget=20, hitl_exhausted=True)
    result = agent._build_result({})
    assert result.success is False
    assert result.reason == "hitl_resume_exhausted"


def test_build_result_no_limit_string_match():
    # Regression guard for the deleted `"limit" in content` heuristic: an
    # assistant message literally containing "limit" must NOT force
    # max_model_calls when the numeric budget is not spent.
    from langchain_core.messages import AIMessage

    agent = _bare_agent(steps=4, budget=20)
    result = agent._build_result({"messages": [AIMessage(content="hit the rate limit page")]})
    assert result.reason == "model_stopped"


# --------------------------------------------------------------------------
# HITL-resume budget: the outer loop terminates as hitl_resume_exhausted when a
# persistent interrupt keeps re-raising past max_hitl_resumes (S1 §3.3).
# --------------------------------------------------------------------------
def test_run_hitl_resume_exhausted(monkeypatch):
    from phone_agent.v2.agent import ThinPhoneAgent

    agent = ThinPhoneAgent.__new__(ThinPhoneAgent)
    agent.config = FakeConfig(max_model_calls=20)
    agent.config.max_hitl_resumes = 2
    agent.run_id = "run-hitl"
    agent.trace_path = None
    agent._trace = SimpleNamespace(_step=1)
    agent.session = SimpleNamespace(finished=False, finish_summary=None, takeover_reason=None)
    agent._budget = None

    invokes = {"n": 0}

    # A fake compiled graph whose invoke always returns an interrupt payload, so
    # the resume loop can never clear it and must hit the resume budget.
    def _always_interrupt(payload, config):  # noqa: ANN001
        invokes["n"] += 1
        return {"__interrupt__": [SimpleNamespace(value={"action_requests": [{"name": "take_over"}], "review_configs": []})]}

    agent.agent = SimpleNamespace(invoke=_always_interrupt)
    monkeypatch.setattr(agent, "_seed_task_doc", lambda task: None)
    monkeypatch.setattr(agent, "_initial_messages", lambda task: [])

    result = agent.run("卡住的任务", hitl_handler=lambda prompt: "approve")
    assert result.success is False
    assert result.reason == "hitl_resume_exhausted"
    # initial invoke + max_hitl_resumes resumes = 1 + 2 = 3 invokes total.
    assert invokes["n"] == 3
