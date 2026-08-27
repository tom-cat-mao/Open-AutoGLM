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


def test_thin_loop_read_tap_finish(scripted_agent):
    agent, session = scripted_agent
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
