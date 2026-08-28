"""Tests for the finish two-step review packet + doubts (S2 §1) and evidence anchors.

All fakes — no real device / MLX / network. Exercises ``phone_agent.v2.review``
(``build_review_package`` / ``finish_doubts`` / ``is_launcher``) and the
two-step ``finish`` control tool's world-mirror + seq-guard behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phone_agent.v2.review import build_review_package, finish_doubts, is_launcher
from phone_agent.v2.taskdoc import TaskDoc, TaskItem
from phone_agent.v2.tools.control import build_control_tools


# --------------------------------------------------------------------------
# Fakes: a session shape the review packet reads.
# --------------------------------------------------------------------------
@dataclass
class _Obs:
    current_app: str = "com.example.app"
    marks: dict = field(default_factory=dict)
    screen_seq: int = 1


@dataclass
class _ReviewSession:
    current_app: str = "com.example.app"
    marks: dict = field(default_factory=dict)
    screen_seq: int = 0
    last_tool_ok: bool | None = None
    task_doc: Any = None
    seen_states: set = field(default_factory=set)
    nudged: bool = False
    finished: bool = False
    finish_summary: str | None = None
    finish_reviewed: bool = False
    finish_review_seq: int = -1
    finish_dispute_count: int = 0
    _observe_fail: bool = False

    def observe(self) -> _Obs:
        if self._observe_fail:
            raise RuntimeError("screenshot invalid: screenshot_unavailable")
        self.screen_seq += 1
        return _Obs(
            current_app=self.current_app,
            marks=self.marks,
            screen_seq=self.screen_seq,
        )


class _Cfg:
    finish_verify = "auto"


# --------------------------------------------------------------------------
# is_launcher
# --------------------------------------------------------------------------
def test_is_launcher_matches_launcher_tokens():
    assert is_launcher("com.android.launcher")
    assert is_launcher("com.google.android.apps.nexuslauncher")
    assert is_launcher("com.miui.home")
    assert is_launcher("桌面")
    assert is_launcher("Launcher")


def test_is_launcher_rejects_regular_app_and_none():
    assert not is_launcher("com.android.settings")
    assert not is_launcher(None)
    assert not is_launcher("")


# --------------------------------------------------------------------------
# build_review_package: four sections + one observe
# --------------------------------------------------------------------------
def test_review_packet_has_four_sections():
    session = _ReviewSession(current_app="com.android.settings")
    text = build_review_package(session, _Cfg())
    assert "[FINISH 复核包]" in text
    assert "## 世界事实" in text
    assert "## 路线状态" in text
    assert "## 疑点" in text
    assert "## 选项" in text
    # Exactly one observe -> screen_seq advanced by one.
    assert session.screen_seq == 1
    assert "com.android.settings" in text


def test_review_packet_last_action_mirrored():
    ok = _ReviewSession(last_tool_ok=True)
    assert "最近动作：成功" in build_review_package(ok, _Cfg())
    unknown = _ReviewSession(last_tool_ok=None)
    assert "最近动作：未知" in build_review_package(unknown, _Cfg())


def test_review_packet_degrades_when_observe_fails():
    session = _ReviewSession(_observe_fail=True)
    text = build_review_package(session, _Cfg())
    # Fail-closed: never fabricate a screen, surface the unavailability + hard doubt.
    assert "world mirror unavailable" in text
    assert "截图/观测无效" in text


def test_review_packet_shows_route_evidence():
    doc = TaskDoc(
        goal_base="打开设置",
        items=[TaskItem("s1", "打开设置", status="completed", evidence_note="设置页标题可见")],
    )
    session = _ReviewSession(task_doc=doc)
    text = build_review_package(session, _Cfg())
    assert "已完成 s1" in text
    assert "证据：设置页标题可见" in text


# --------------------------------------------------------------------------
# finish_doubts: hard contradictions vs soft doubts (all cheap-local)
# --------------------------------------------------------------------------
def test_doubts_hard_on_failed_last_action():
    session = _ReviewSession(last_tool_ok=False)
    obs = _Obs(current_app="com.android.settings")
    doubts = finish_doubts(session, obs, None)
    assert any("last_tool_ok=False" in h for h in doubts["hard"])


def test_doubts_hard_on_launcher_foreground():
    session = _ReviewSession()
    obs = _Obs(current_app="com.android.launcher")
    doubts = finish_doubts(session, obs, None)
    assert any("桌面" in h or "Launcher" in h for h in doubts["hard"])


def test_doubts_hard_on_observe_error():
    session = _ReviewSession()
    doubts = finish_doubts(session, None, RuntimeError("screenshot invalid"))
    assert any("截图" in h for h in doubts["hard"])


def test_doubts_soft_on_missing_evidence_and_blocked():
    doc = TaskDoc(
        goal_base="g",
        items=[
            TaskItem("s1", "A", status="completed", evidence_note="x"),
            TaskItem("s2", "B", status="blocked", reason="卡住"),
        ],
    )
    # Bypass validate (which would reject a note-less completed item): inject a
    # completed item with no evidence_note directly to prove the soft-doubt path.
    doc.items.append(TaskItem("s3", "C", status="completed"))
    session = _ReviewSession(task_doc=doc)
    obs = _Obs(current_app="com.example.app", marks={"m1": 1, "m2": 2})
    doubts = finish_doubts(session, obs, None)
    assert any("blocked" in s for s in doubts["soft"])
    assert any("evidence_note" in s for s in doubts["soft"])


def test_doubts_soft_on_too_few_marks():
    session = _ReviewSession()
    obs = _Obs(current_app="com.example.app", marks={})
    doubts = finish_doubts(session, obs, None)
    assert any("marks" in s for s in doubts["soft"])


# --------------------------------------------------------------------------
# two-step finish integration through the control tool (fresh vs stale seq)
# --------------------------------------------------------------------------
def _finish(session, config=None):
    tools = {t.name: t for t in build_control_tools(session, config)}
    return tools["finish"]


def test_finish_confirm_lands_when_seq_fresh():
    session = _ReviewSession()
    finish = _finish(session, _Cfg())
    first = finish.invoke({"summary": "done", "evidence": ["proof"]})
    assert "[FINISH 复核包]" in first
    assert session.finished is False
    # No observation between packet and confirm -> seq stays fresh -> lands.
    out = finish.invoke({"summary": "done", "evidence": ["proof"], "confirm": True})
    assert out == "已确认完成"
    assert session.finished is True


def test_finish_confirm_stale_after_observe_reissues_packet():
    session = _ReviewSession()
    finish = _finish(session, _Cfg())
    finish.invoke({"summary": "done", "evidence": ["proof"]})
    session.observe()  # bumps screen_seq -> recorded review_seq goes stale
    out = finish.invoke({"summary": "done", "evidence": ["proof"], "confirm": True})
    assert "[FINISH 复核包]" in out
    assert session.finished is False


def test_finish_off_mode_lands_single_step():
    class Off:
        finish_verify = "off"

    session = _ReviewSession()
    finish = _finish(session, Off())
    out = finish.invoke({"summary": "done", "evidence": ["proof"]})
    assert out == "已记录完成声明"
    assert session.finished is True


def test_finish_confirm_none_config_defaults_to_two_step():
    # build_control_tools(session, config=None) must not crash: finish reads
    # finish_verify via getattr default "auto" -> two-step.
    session = _ReviewSession()
    finish = _finish(session, None)
    first = finish.invoke({"summary": "done", "evidence": ["proof"]})
    assert "[FINISH 复核包]" in first
    out = finish.invoke({"summary": "done", "evidence": ["proof"], "confirm": True})
    assert out == "已确认完成"
    assert session.finished is True
