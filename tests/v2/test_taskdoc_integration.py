"""W2 TaskDoc integration tests (spec §3 W2): renderer, nudge, finish guard, seeding.

All fakes — no real device, MLX, or network. ``phone_agent.v2.taskdoc`` is owned
by the concurrent W1 worktree and may not exist here, so these tests use a local
``FakeTaskDoc`` matching the spec §2.1 interface
(``TaskDoc(goal_base=..., items=[...], facts=[...])`` with
``validate()`` / ``has_open_items()`` / ``open_items_summary()`` / ``render(lang)``)
and, for the seeding test, inject a fake ``phone_agent.v2.taskdoc`` module into
``sys.modules`` (the same technique ``tests/v2/test_agent_loop.py`` uses for the
core/tools modules).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from phone_agent.v2.middleware.taskdoc import build_taskdoc_middleware


# --------------------------------------------------------------------------
# Fake TaskDoc matching spec §2.1 (goal + route + facts, three-段 render).
# --------------------------------------------------------------------------
@dataclass
class FakeTaskItem:
    id: str
    content: str
    status: str = "pending"
    reason: str | None = None


@dataclass
class FakeTaskDoc:
    goal_base: str = ""
    amendments: list[str] = field(default_factory=list)
    items: list[FakeTaskItem] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)

    def validate(self) -> str | None:
        return None

    def has_open_items(self) -> bool:
        return any(i.status in {"pending", "in_progress"} for i in self.items)

    def open_items_summary(self) -> str:
        return "; ".join(
            f"{i.id}:{i.content}[{i.status}]"
            for i in self.items
            if i.status in {"pending", "in_progress"}
        )

    def render(self, lang: str = "cn") -> str:
        if not (self.goal_base or self.items or self.facts):
            return ""
        lines = ["## 目标", f"base: {self.goal_base}"]
        if self.items:
            lines.append("## 路线")
            for i in self.items:
                lines.append(f"- {i.id} [{i.status}] {i.content}")
        if self.facts:
            lines.append("## 关键事实")
            lines.extend(f"- {f}" for f in self.facts)
        return "\n".join(lines)


@dataclass
class FakeSession:
    """Minimal §6 surface the TaskDoc middleware / finish guard read."""

    task_doc: Any = None
    seen_states: set = field(default_factory=set)
    nudged: bool = False
    finished: bool = False
    finish_summary: str | None = None


def _open_doc() -> FakeTaskDoc:
    return FakeTaskDoc(
        goal_base="打开设置并连上 WLAN",
        items=[
            FakeTaskItem("1", "打开设置", status="completed"),
            FakeTaskItem("2", "连接 WLAN", status="pending"),
        ],
        facts=["WLAN 名称 HomeNet"],
    )


def _done_doc() -> FakeTaskDoc:
    return FakeTaskDoc(
        goal_base="打开设置",
        items=[FakeTaskItem("1", "打开设置", status="completed")],
    )


# --------------------------------------------------------------------------
# §2.3 render hook: non-empty doc injects [TASK_DOC]; empty doc does not.
# --------------------------------------------------------------------------
def test_render_injects_taskdoc_block_at_tail():
    session = FakeSession(task_doc=_open_doc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=5)

    result = mw.before_model({"messages": []}, runtime=None)

    assert result is not None
    messages = result["messages"]
    injected = messages[-1]
    assert isinstance(injected, SystemMessage)
    assert injected.content.startswith("[TASK_DOC]\n")
    assert "## 目标" in injected.content
    assert "base: 打开设置并连上 WLAN" in injected.content
    assert "## 路线" in injected.content
    assert "## 关键事实" in injected.content
    # No stale copy to remove on the first injection.
    assert not any(isinstance(m, RemoveMessage) for m in messages)


def test_render_no_injection_for_empty_doc():
    session = FakeSession(task_doc=FakeTaskDoc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn")
    assert mw.before_model({"messages": []}, runtime=None) is None


def test_render_no_injection_when_task_doc_missing():
    session = FakeSession(task_doc=None)
    mw = build_taskdoc_middleware(session, lang="cn")
    assert mw.before_model({"messages": []}, runtime=None) is None


def test_render_refreshes_pinned_block_removing_stale_copy():
    session = FakeSession(task_doc=_open_doc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=99)

    first = mw.before_model({"messages": []}, runtime=None)
    first_id = first["messages"][-1].id

    second = mw.before_model({"messages": []}, runtime=None)
    ids = [getattr(m, "id", None) for m in second["messages"]]
    # Second turn removes the prior pinned copy and appends a fresh one at tail.
    removes = [m for m in second["messages"] if isinstance(m, RemoveMessage)]
    assert len(removes) == 1
    assert removes[0].id == first_id
    assert isinstance(second["messages"][-1], SystemMessage)
    assert second["messages"][-1].id != first_id
    assert first_id not in [second["messages"][-1].id]


# --------------------------------------------------------------------------
# §2.3 stagnation nudge: fires once when seen_states is stable + open items.
# --------------------------------------------------------------------------
def test_nudge_fires_exactly_once_on_stagnation():
    # seen_states never grows; open items present -> nudge after nudge_steps.
    session = FakeSession(task_doc=_open_doc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=5)

    nudged_turns = []
    for turn in range(10):
        result = mw.before_model({"messages": []}, runtime=None)
        text = result["messages"][-1].content
        if "无新状态" in text:
            nudged_turns.append(turn)

    assert len(nudged_turns) == 1, nudged_turns
    assert session.nudged is True


def test_nudge_suppressed_without_open_items():
    # All items completed -> has_open_items False -> never nudge even if stagnant.
    session = FakeSession(task_doc=_done_doc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=3)

    for _ in range(10):
        result = mw.before_model({"messages": []}, runtime=None)
        assert "无新状态" not in result["messages"][-1].content
    assert session.nudged is False


def test_nudge_reset_when_new_state_appears():
    # A growing seen_states resets the stagnation counter, so no nudge yet.
    session = FakeSession(task_doc=_open_doc(), seen_states=set())
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=3)

    for step in range(6):
        # Add a new state every turn -> never stagnant.
        session.seen_states.add(("app", f"h{step}"))
        result = mw.before_model({"messages": []}, runtime=None)
        assert "无新状态" not in result["messages"][-1].content
    assert session.nudged is False


def test_nudge_text_is_non_directive_option_list():
    session = FakeSession(task_doc=_open_doc(), seen_states={("app", "h1")})
    mw = build_taskdoc_middleware(session, lang="cn", nudge_steps=1)
    # nudge_steps=1: first call establishes max_seen (stagnant=0), second nudges.
    mw.before_model({"messages": []}, runtime=None)
    result = mw.before_model({"messages": []}, runtime=None)
    text = result["messages"][-1].content
    assert "无新状态" in text
    # Non-directive: lists the option space, no imperative command.
    for option in ("update_task_doc", "locate", "ask_user", "take_over", "finish"):
        assert option in text


# --------------------------------------------------------------------------
# §2.4 finish guard: open items block finish; all-completed allows it.
# --------------------------------------------------------------------------
def _finish_tool(session):
    from phone_agent.v2.tools.control import build_control_tools

    tools = {t.name: t for t in build_control_tools(session, config=None)}
    return tools["finish"]


def test_finish_blocked_when_open_items():
    session = FakeSession(task_doc=_open_doc())
    finish = _finish_tool(session)
    out = finish.invoke({"summary": "完成", "evidence": ["屏幕显示已连接"]})
    assert "路线仍有未完成项" in out
    assert "2:连接 WLAN[pending]" in out
    assert session.finished is False


def test_finish_allowed_when_all_completed():
    session = FakeSession(task_doc=_done_doc())
    finish = _finish_tool(session)
    out = finish.invoke({"summary": "已打开设置", "evidence": ["设置页可见"]})
    assert out == "已记录完成声明"
    assert session.finished is True
    assert session.finish_summary == "已打开设置"


def test_finish_still_rejects_empty_evidence_before_taskdoc_guard():
    session = FakeSession(task_doc=_done_doc())
    finish = _finish_tool(session)
    out = finish.invoke({"summary": "x", "evidence": []})
    assert out.startswith("error:")
    assert session.finished is False


def test_finish_without_task_doc_uses_only_evidence_gate():
    session = FakeSession(task_doc=None)
    finish = _finish_tool(session)
    out = finish.invoke({"summary": "done", "evidence": ["ok"]})
    assert out == "已记录完成声明"
    assert session.finished is True


# --------------------------------------------------------------------------
# §2.5 seeding: run() seeds session.task_doc.goal_base == task.
# Reuses the fake-agent-loop technique from tests/v2/test_agent_loop.py.
# --------------------------------------------------------------------------
class _ScriptedModel(BaseChatModel):
    """Replays a fixed list of AIMessages, one per model call."""

    responses: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        response = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted-taskdoc-model"


@dataclass
class _SeedFakeObservation:
    screenshot_b64: str = "QUJD"
    width: int = 1080
    height: int = 2400
    current_app: str = "com.android.settings"
    screen_seq: int = 0
    marks: dict = field(default_factory=dict)


@dataclass
class _SeedFakeSession:
    config: Any = None
    marks: dict = field(default_factory=dict)
    screen_seq: int = 0
    finished: bool = False
    finish_summary: str | None = None
    takeover_reason: str | None = None
    task_doc: Any = None
    seen_states: set = field(default_factory=set)
    nudged: bool = False

    def observe(self) -> _SeedFakeObservation:
        self.screen_seq += 1
        self.seen_states.add(("com.android.settings", f"h{self.screen_seq}"))
        return _SeedFakeObservation(screen_seq=self.screen_seq)


@dataclass
class _SeedFakeConfig:
    lang: str = "cn"
    max_model_calls: int = 20
    trace_dir: str = ".traces"
    trace_enabled: bool = False
    taskdoc_enabled: bool = True
    taskdoc_nudge_steps: int = 5


def _fake_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture
def seeded_agent(tmp_path, monkeypatch):
    session = _SeedFakeSession()

    responses = [AIMessage(content="看看再说")]  # no tool_call -> loop ends immediately
    model = _ScriptedModel(responses=responses)

    modules = {
        "phone_agent.v2.model": _fake_module(
            "phone_agent.v2.model", build_chat_model=lambda config: model
        ),
        "phone_agent.v2.session": _fake_module(
            "phone_agent.v2.session", PhoneSession=lambda config: session
        ),
        "phone_agent.v2.tools": _fake_module(
            "phone_agent.v2.tools", build_tools=lambda sess, config: []
        ),
        "phone_agent.v2.prompts": _fake_module(
            "phone_agent.v2.prompts", get_system_prompt=lambda lang="cn": "你是手机智能体。"
        ),
        # Fake the W1 taskdoc module so run()'s lazy import seeds a real object.
        "phone_agent.v2.taskdoc": _fake_module(
            "phone_agent.v2.taskdoc", TaskDoc=FakeTaskDoc
        ),
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    from phone_agent.v2.agent import ThinPhoneAgent

    config = _SeedFakeConfig(trace_dir=str(tmp_path), trace_enabled=False)
    agent = ThinPhoneAgent(config)
    return agent, session


def test_run_seeds_task_doc_goal_base(seeded_agent):
    agent, session = seeded_agent
    task = "打开设置并连上 WLAN"
    agent.run(task, hitl_handler=lambda prompt: "approve")

    assert session.task_doc is not None
    assert isinstance(session.task_doc, FakeTaskDoc)
    assert session.task_doc.goal_base == task


def test_run_skips_seeding_when_taskdoc_disabled(tmp_path, monkeypatch):
    session = _SeedFakeSession()
    model = _ScriptedModel(responses=[AIMessage(content="done")])

    modules = {
        "phone_agent.v2.model": _fake_module(
            "phone_agent.v2.model", build_chat_model=lambda config: model
        ),
        "phone_agent.v2.session": _fake_module(
            "phone_agent.v2.session", PhoneSession=lambda config: session
        ),
        "phone_agent.v2.tools": _fake_module(
            "phone_agent.v2.tools", build_tools=lambda sess, config: []
        ),
        "phone_agent.v2.prompts": _fake_module(
            "phone_agent.v2.prompts", get_system_prompt=lambda lang="cn": "sys"
        ),
        "phone_agent.v2.taskdoc": _fake_module(
            "phone_agent.v2.taskdoc", TaskDoc=FakeTaskDoc
        ),
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    from phone_agent.v2.agent import ThinPhoneAgent

    config = _SeedFakeConfig(
        trace_dir=str(tmp_path), trace_enabled=False, taskdoc_enabled=False
    )
    agent = ThinPhoneAgent(config)
    agent.run("任意任务", hitl_handler=lambda prompt: "approve")
    # Disabled: run() must not seed the board.
    assert session.task_doc is None


# --------------------------------------------------------------------------
# Config: taskdoc_* three-level resolution (spec §5.4).
# --------------------------------------------------------------------------
_TASKDOC_ENV = ["PHONE_AGENT_TASKDOC", "PHONE_AGENT_TASKDOC_NUDGE_STEPS"]


@pytest.fixture(autouse=True)
def _clean_taskdoc_env(monkeypatch):
    for key in _TASKDOC_ENV:
        monkeypatch.delenv(key, raising=False)
    yield


def test_config_taskdoc_defaults():
    from phone_agent.v2.config import V2Config

    cfg = V2Config.from_env()
    assert cfg.taskdoc_enabled is True
    assert cfg.taskdoc_nudge_steps == 5


@pytest.mark.parametrize(
    "raw,expected",
    [("0", False), ("false", False), ("no", False), ("off", False),
     ("FALSE", False), ("1", True), ("true", True), ("yes", True)],
)
def test_config_taskdoc_enabled_parsing(monkeypatch, raw, expected):
    from phone_agent.v2.config import V2Config

    monkeypatch.setenv("PHONE_AGENT_TASKDOC", raw)
    assert V2Config.from_env().taskdoc_enabled is expected


def test_config_taskdoc_nudge_steps_env_and_override(monkeypatch):
    from phone_agent.v2.config import V2Config

    monkeypatch.setenv("PHONE_AGENT_TASKDOC_NUDGE_STEPS", "8")
    assert V2Config.from_env().taskdoc_nudge_steps == 8
    # CLI override beats env.
    assert V2Config.from_env({"taskdoc_nudge_steps": 3}).taskdoc_nudge_steps == 3
