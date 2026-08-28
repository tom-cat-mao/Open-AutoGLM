"""Tests for the finish verifier (S2 §4): trigger, verdict, dispute, fail-open.

All fakes — the verifier model is a stub, so no network / real model is used.
Covers ``should_verify_finish`` triggers (high-risk goal / hard-contradiction /
three modes), ``verify_finish`` verdict parsing + fail-open, and the ``finish``
control-tool integration (in-band REJECT, 2-strike takeover, APPROVE lands).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import phone_agent.v2.verify as verify_mod
from phone_agent.v2.taskdoc import TaskDoc, TaskItem
from phone_agent.v2.tools.control import build_control_tools
from phone_agent.v2.verify import (
    DISPUTE_TAKEOVER_REASON,
    Verdict,
    should_verify_finish,
    verify_finish,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
@dataclass
class _Obs:
    current_app: str = "com.example.app"
    marks: dict = field(default_factory=dict)
    screen_seq: int = 1
    screenshot_b64: str = "QUJD"
    mime_type: str = "image/png"


@dataclass
class _Sess:
    screen_seq: int = 0
    last_tool_ok: Any = None
    task_doc: Any = None
    finished: bool = False
    finish_summary: Any = None
    finish_reviewed: bool = False
    finish_review_seq: int = -1
    finish_dispute_count: int = 0
    finish_hard_doubts: list = field(default_factory=list)
    takeover_reason: Any = None
    nudged: bool = False
    seen_states: set = field(default_factory=set)
    marks: dict = field(default_factory=dict)
    _observe_fail: bool = False

    def observe(self) -> _Obs:
        if self._observe_fail:
            raise RuntimeError("screenshot invalid")
        self.screen_seq += 1
        return _Obs(screen_seq=self.screen_seq)


class _Cfg:
    finish_verify = "auto"
    verifier_model = None
    model_name = "main-model"


class _Off:
    finish_verify = "off"
    verifier_model = None
    model_name = "m"


class _Always:
    finish_verify = "always"
    verifier_model = None
    model_name = "m"


class _FakeModel:
    """Stub chat model returning a fixed content string."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list = []

    def invoke(self, messages):
        from types import SimpleNamespace

        self.calls.append(messages)
        return SimpleNamespace(content=self.text)


class _BoomModel:
    def invoke(self, messages):
        raise RuntimeError("verifier net down")


# --------------------------------------------------------------------------
# should_verify_finish: triggers + modes
# --------------------------------------------------------------------------
def test_high_risk_goal_triggers_verify():
    s = _Sess(task_doc=TaskDoc(goal_base="帮我在支付宝完成支付"))
    assert should_verify_finish(s, _Cfg()) is True


def test_high_risk_amendment_triggers_verify():
    doc = TaskDoc(goal_base="打开应用", amendments=["确认删除这条记录"])
    s = _Sess(task_doc=doc)
    assert should_verify_finish(s, _Cfg()) is True


def test_ordinary_goal_does_not_trigger_verify():
    s = _Sess(task_doc=TaskDoc(goal_base="查看北京今天的天气"))
    assert should_verify_finish(s, _Cfg()) is False


def test_hard_contradiction_triggers_verify():
    s = _Sess(
        task_doc=TaskDoc(goal_base="查看天气"),
        finish_hard_doubts=["最近一次动作失败（last_tool_ok=False）"],
    )
    assert should_verify_finish(s, _Cfg()) is True


def test_off_mode_never_triggers():
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    assert should_verify_finish(s, _Off()) is False


def test_always_mode_triggers_on_ordinary_goal():
    s = _Sess(task_doc=TaskDoc(goal_base="查看天气"))
    assert should_verify_finish(s, _Always()) is True


# --------------------------------------------------------------------------
# verify_finish: verdict parsing + independent context + fail-open
# --------------------------------------------------------------------------
def test_verify_approve():
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    v = verify_finish(s, _Cfg(), model=_FakeModel("APPROVE 支付成功页可见"))
    assert v.approve is True


def test_verify_reject():
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    v = verify_finish(s, _Cfg(), model=_FakeModel("REJECT 未见支付成功页"))
    assert v.approve is False
    assert "REJECT" in v.reason or "未见" in v.reason


def test_verify_ambiguous_answer_is_fail_open():
    # An unparseable answer should approve (fail-open bias, §4.5).
    s = _Sess(task_doc=TaskDoc(goal_base="查看天气"))
    v = verify_finish(s, _Cfg(), model=_FakeModel("嗯……不太确定"))
    assert v.approve is True


def test_verify_call_failure_is_fail_open():
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    v = verify_finish(s, _Cfg(), model=_BoomModel())
    assert v.approve is True
    assert "fail-open" in v.reason


def test_verify_context_excludes_transcript_includes_goal_and_screenshot():
    doc = TaskDoc(
        goal_base="在支付宝完成支付",
        items=[TaskItem("s1", "点击确认支付", status="completed", evidence_note="支付成功页")],
    )
    s = _Sess(task_doc=doc)
    model = _FakeModel("APPROVE ok")
    verify_finish(s, _Cfg(), model=model)
    # One invoke with a system + human message; human carries goal text + image.
    assert len(model.calls) == 1
    messages = model.calls[0]
    assert len(messages) == 2  # system + human, no transcript
    human = messages[1]
    text_blocks = [b for b in human.content if b.get("type") == "text"]
    joined = "\n".join(b["text"] for b in text_blocks)
    assert "在支付宝完成支付" in joined
    assert "支付成功页" in joined  # evidence_note surfaced
    image_blocks = [b for b in human.content if b.get("type") == "image_url"]
    assert len(image_blocks) == 1  # K=1 current frame


def test_verify_observe_failure_yields_route_only():
    doc = TaskDoc(goal_base="在支付宝完成支付")
    s = _Sess(task_doc=doc, _observe_fail=True)
    model = _FakeModel("APPROVE ok")
    verify_finish(s, _Cfg(), model=model)
    human = model.calls[0][1]
    image_blocks = [b for b in human.content if b.get("type") == "image_url"]
    assert image_blocks == []  # no fabricated screenshot


# --------------------------------------------------------------------------
# finish control-tool integration
# --------------------------------------------------------------------------
def _finish_tool(session, config):
    return {t.name: t for t in build_control_tools(session, config)}["finish"]


def _patch_verify(monkeypatch, verdict: Verdict, spy: dict | None = None):
    def _fake(session, config, **kw):
        if spy is not None:
            spy["n"] = spy.get("n", 0) + 1
        return verdict

    monkeypatch.setattr(verify_mod, "verify_finish", _fake)


def test_finish_high_risk_reject_returns_in_band(monkeypatch):
    _patch_verify(monkeypatch, Verdict(False, "未见支付成功"))
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    finish = _finish_tool(s, _Cfg())
    first = finish.invoke({"summary": "done", "evidence": ["pay ok"]})
    assert "[FINISH 复核包]" in first
    out = finish.invoke({"summary": "done", "evidence": ["pay ok"], "confirm": True})
    assert "验收未通过" in out
    assert s.finished is False
    assert s.finish_dispute_count == 1


def test_finish_two_rejections_escalate_to_takeover(monkeypatch):
    _patch_verify(monkeypatch, Verdict(False, "未见支付成功"))
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    finish = _finish_tool(s, _Cfg())
    finish.invoke({"summary": "done", "evidence": ["pay ok"]})
    finish.invoke({"summary": "done", "evidence": ["pay ok"], "confirm": True})
    out2 = finish.invoke({"summary": "done", "evidence": ["pay ok"], "confirm": True})
    assert "人工" in out2
    assert s.takeover_reason == DISPUTE_TAKEOVER_REASON
    assert s.finish_dispute_count == 2
    assert s.finished is False


def test_finish_high_risk_approve_lands(monkeypatch):
    _patch_verify(monkeypatch, Verdict(True, "ok"))
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    finish = _finish_tool(s, _Cfg())
    finish.invoke({"summary": "done", "evidence": ["pay ok"]})
    out = finish.invoke({"summary": "done", "evidence": ["pay ok"], "confirm": True})
    assert out == "已确认完成"
    assert s.finished is True


def test_finish_ordinary_goal_skips_verifier(monkeypatch):
    spy: dict = {}
    _patch_verify(monkeypatch, Verdict(True, "x"), spy=spy)
    s = _Sess(task_doc=TaskDoc(goal_base="查看北京天气"))
    finish = _finish_tool(s, _Cfg())
    finish.invoke({"summary": "d", "evidence": ["e"]})
    out = finish.invoke({"summary": "d", "evidence": ["e"], "confirm": True})
    assert out == "已确认完成"
    assert s.finished is True
    assert spy.get("n", 0) == 0  # verifier never called for an ordinary goal


def test_finish_off_mode_skips_verifier(monkeypatch):
    spy: dict = {}
    _patch_verify(monkeypatch, Verdict(False, "should-not-run"), spy=spy)
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    finish = _finish_tool(s, _Off())
    out = finish.invoke({"summary": "d", "evidence": ["e"]})
    # off mode -> single-step landing, no packet, no verifier.
    assert out == "已记录完成声明"
    assert s.finished is True
    assert spy.get("n", 0) == 0


def test_finish_verifier_import_or_error_is_fail_open(monkeypatch):
    # A verifier that raises is swallowed (fail-open): the confirm still lands.
    def _boom(session, config, **kw):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(verify_mod, "should_verify_finish", lambda s, c: True)
    monkeypatch.setattr(verify_mod, "verify_finish", _boom)
    s = _Sess(task_doc=TaskDoc(goal_base="在支付宝完成支付"))
    finish = _finish_tool(s, _Cfg())
    finish.invoke({"summary": "done", "evidence": ["pay ok"]})
    out = finish.invoke({"summary": "done", "evidence": ["pay ok"], "confirm": True})
    assert out == "已确认完成"
    assert s.finished is True
