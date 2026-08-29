"""U3 flow-line tests: intent-in-args output contract + transcript-derived flow line.

All fakes — no real device, MLX, or network. Covers three things the U3 output
contract promises:

1. Every tool carries ``intent``/``note`` params (schema-level) and the model can
   pass them without breaking the tool body.
2. The TaskDoc middleware derives a ``## 流程线`` block from the transcript's
   ``AIMessage.tool_calls`` + matching ``ToolMessage`` receipts — no new session
   state — rendering ``#N <intent> → <tool><target> → <result>``.
3. A missing intent renders as ``（未声明）`` (tolerant), and the section caps at
   the trailing :data:`MAX_FLOW_ITEMS` entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from phone_agent.v2.middleware.taskdoc import (
    MAX_FLOW_ITEMS,
    _derive_flow_lines,
    build_taskdoc_middleware,
)
from phone_agent.v2.tools import build_tools

from tests.v2._doubles import FakeConfig, FakePhoneSession, make_mark


# --------------------------------------------------------------------------
# 1. intent/note are on every tool's schema (output contract, args-level).
# --------------------------------------------------------------------------
def _tool_map(session, config=None):
    return {t.name: t for t in build_tools(session, config or FakeConfig())}


def test_all_tools_expose_intent_and_note_args():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    # Every tool the model calls under the U3 contract exposes intent + note.
    for name in (
        "read_screen",
        "locate",
        "tap",
        "long_press",
        "type_text",
        "scroll",
        "swipe",
        "back",
        "home",
        "wait",
        "launch_app",
        "finish",
        "ask_user",
        "take_over",
        "update_task_doc",
    ):
        args = tools[name].args
        assert "intent" in args, f"{name} missing intent"
        assert "note" in args, f"{name} missing note"


def test_tap_accepts_intent_and_note_without_breaking_body():
    marks = {"ax_3": make_mark("ax_3", text="上海", center=(500, 300))}
    session = FakePhoneSession(marks, width=1080, height=2400)
    tools = _tool_map(session)
    out = tools["tap"].invoke(
        {
            "target_mark_id": "ax_3",
            "intent": "把出发地改成上海",
            "note": "顶部是城市选择器",
        }
    )
    # intent/note are contract metadata: the device action still happens and the
    # receipt names what was acted on.
    assert ("tap", 540, 720) in session.device_factory.calls
    text = "\n".join(
        b.get("text", "") for b in out if isinstance(b, dict) and b.get("type") == "text"
    )
    assert "已点击「上海」(ax_3)" in text


def test_tap_receipt_names_element_from_description():
    marks = {"ax_7": make_mark("ax_7", text="确认付款", center=(200, 400))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke(
        {"target_description": "确认付款", "intent": "提交订单"}
    )
    text = "\n".join(
        b.get("text", "") for b in out if isinstance(b, dict) and b.get("type") == "text"
    )
    assert "已点击「确认付款」(ax_7)" in text


# --------------------------------------------------------------------------
# 2. Flow-line derivation from the transcript (tool_call/tool_result pairs).
# --------------------------------------------------------------------------
def _ai(name: str, args: dict, cid: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": cid, "type": "tool_call"}],
    )


def _tool_msg(cid: str, content) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=cid)


def test_derive_flow_lines_reads_intent_and_target_and_result():
    messages = [
        _ai("tap", {"target_description": "上海", "intent": "把出发地改成上海"}, "c1"),
        _tool_msg("c1", [{"type": "text", "text": "OK. 已点击「上海」(ax_3)"}]),
    ]
    lines = _derive_flow_lines(messages, "cn")
    assert len(lines) == 1
    assert lines[0] == "#1 把出发地改成上海 → tap「上海」 → ok"


def test_derive_flow_lines_missing_intent_renders_undeclared():
    messages = [
        _ai("tap", {"target_mark_id": "ax_9"}, "c1"),  # no intent
        _tool_msg("c1", "OK. 已点击「设置」(ax_9)"),
    ]
    lines = _derive_flow_lines(messages, "cn")
    assert lines[0].startswith("#1 （未声明） → tap")


def test_derive_flow_lines_pending_call_without_result():
    # A tool_call with no matching ToolMessage yet -> pending marker, no crash.
    messages = [_ai("scroll", {"direction": "down", "intent": "查看更多"}, "c1")]
    lines = _derive_flow_lines(messages, "cn")
    assert lines == ["#1 查看更多 → scroll down → …"]


def test_derive_flow_lines_error_result_is_surfaced():
    messages = [
        _ai("tap", {"target_mark_id": "ax_x", "intent": "点返回"}, "c1"),
        _tool_msg("c1", "stale mark: 'ax_x' is no longer on the current screen. ..."),
    ]
    lines = _derive_flow_lines(messages, "cn")
    assert lines[0].startswith("#1 点返回 → tap(ax_x) → stale mark:")


def test_derive_flow_lines_note_is_appended():
    messages = [
        _ai(
            "read_screen",
            {"intent": "看看首页", "note": "顶部有登录入口"},
            "c1",
        ),
        _tool_msg("c1", [{"type": "text", "text": "[OBS] app=com.x screen#1"}]),
    ]
    lines = _derive_flow_lines(messages, "cn")
    assert "｜顶部有登录入口" in lines[0]


def test_derive_flow_lines_caps_at_max_items():
    messages: list[Any] = []
    for i in range(MAX_FLOW_ITEMS + 5):
        cid = f"c{i}"
        messages.append(_ai("back", {"intent": f"第{i}步"}, cid))
        messages.append(_tool_msg(cid, "OK. back"))
    lines = _derive_flow_lines(messages, "cn")
    assert len(lines) == MAX_FLOW_ITEMS
    # The trailing window is shown; numbering reflects absolute step index.
    first_step = MAX_FLOW_ITEMS + 5 - MAX_FLOW_ITEMS + 1
    assert lines[0].startswith(f"#{first_step} ")
    assert lines[-1].startswith(f"#{MAX_FLOW_ITEMS + 5} ")


def test_derive_flow_lines_type_text_shows_typed_value():
    messages = [
        _ai("type_text", {"text": "北京南站", "intent": "填入到达站"}, "c1"),
        _tool_msg("c1", "OK. 已输入 '北京南站'"),
    ]
    lines = _derive_flow_lines(messages, "cn")
    assert lines[0] == "#1 填入到达站 → type_text「北京南站」 → ok"


def test_derive_flow_lines_empty_transcript():
    assert _derive_flow_lines([], "cn") == []
    assert _derive_flow_lines([AIMessage(content="just thinking")], "cn") == []


def test_derive_flow_lines_en_uses_english_undeclared():
    messages = [
        _ai("home", {}, "c1"),
        _tool_msg("c1", "OK. home"),
    ]
    lines = _derive_flow_lines(messages, "en")
    assert lines[0].startswith("#1 (no intent) → home")


# --------------------------------------------------------------------------
# 3. Middleware wiring: the pinned block carries the flow line after the doc.
# --------------------------------------------------------------------------
@dataclass
class _FakeItem:
    id: str
    content: str
    status: str = "pending"
    reason: str | None = None
    evidence_note: str | None = None


@dataclass
class _FakeDoc:
    goal_base: str = ""
    amendments: list = field(default_factory=list)
    items: list = field(default_factory=list)
    facts: list = field(default_factory=list)

    def render(self, lang: str = "cn") -> str:
        if not (self.goal_base or self.items or self.facts):
            return ""
        lines = ["## 目标", f"base: {self.goal_base}"]
        if self.items:
            lines.append("## 路线")
            for i in self.items:
                lines.append(f"- [{i.status}] {i.id}: {i.content}")
        return "\n".join(lines)


@dataclass
class _FakeSession:
    task_doc: Any = None


def test_middleware_appends_flow_line_after_taskdoc():
    session = _FakeSession(
        task_doc=_FakeDoc(
            goal_base="订一张去上海的票",
            items=[_FakeItem("s1", "选择出发地", status="in_progress")],
        )
    )
    mw = build_taskdoc_middleware(session, lang="cn")
    messages = [
        _ai("tap", {"target_description": "上海", "intent": "把出发地改成上海"}, "c1"),
        _tool_msg("c1", [{"type": "text", "text": "OK. 已点击「上海」(ax_3)"}]),
    ]
    result = mw.before_model({"messages": messages}, runtime=None)
    block = result["messages"][-1].content
    assert block.startswith("[TASK_DOC]\n")
    assert "## 目标" in block
    assert "## 流程线（最近 1 步）" in block
    assert "#1 把出发地改成上海 → tap「上海」 → ok" in block
    # The flow line comes after the rendered doc, not before it.
    assert block.index("## 目标") < block.index("## 流程线")


def test_middleware_no_flow_line_when_transcript_has_no_tool_calls():
    session = _FakeSession(task_doc=_FakeDoc(goal_base="任意目标"))
    mw = build_taskdoc_middleware(session, lang="cn")
    result = mw.before_model(
        {"messages": [AIMessage(content="只是想一下")]}, runtime=None
    )
    block = result["messages"][-1].content
    assert "## 流程线" not in block
    assert "## 目标" in block
