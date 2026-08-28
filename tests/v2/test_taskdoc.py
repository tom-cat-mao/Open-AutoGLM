"""Tests for the TaskDoc model + ``update_task_doc`` tool (taskdoc spec §3 W1).

All fakes; no real device / MLX. Covers ``TaskDoc.validate`` / ``has_open_items``
/ ``open_items_summary`` / ``render``, and the ``update_task_doc`` tool's
replace/append/validate-fail-closed semantics plus registration into
``build_tools``.
"""

from __future__ import annotations

from phone_agent.v2.taskdoc import TaskDoc, TaskItem
from phone_agent.v2.tools import build_tools
from phone_agent.v2.tools.taskdoc import make_update_task_doc_tool

from tests.v2._doubles import FakeConfig, FakePhoneSession


# --------------------------------------------------------------------------
# TaskDoc.validate
# --------------------------------------------------------------------------


def test_validate_ok_returns_none():
    doc = TaskDoc(
        goal_base="打开设置连上 WLAN",
        items=[
            TaskItem("s1", "打开设置", status="completed", evidence_note="设置页标题可见"),
            TaskItem("s2", "进入 WLAN", status="in_progress"),
            TaskItem("s3", "选择网络", status="pending"),
        ],
        facts=["WiFi 名: HomeNet"],
    )
    assert doc.validate() is None


def test_validate_rejects_multiple_in_progress():
    doc = TaskDoc(
        items=[
            TaskItem("s1", "A", status="in_progress"),
            TaskItem("s2", "B", status="in_progress"),
        ]
    )
    err = doc.validate()
    assert err is not None
    assert "in_progress" in err


def test_validate_rejects_over_fifteen_items():
    doc = TaskDoc(items=[TaskItem(f"s{i}", f"step {i}") for i in range(16)])
    err = doc.validate()
    assert err is not None
    assert "15" in err


def test_validate_allows_exactly_fifteen_items():
    doc = TaskDoc(items=[TaskItem(f"s{i}", f"step {i}") for i in range(15)])
    assert doc.validate() is None


def test_validate_rejects_blocked_without_reason():
    doc = TaskDoc(items=[TaskItem("s1", "登录", status="blocked")])
    err = doc.validate()
    assert err is not None
    assert "reason" in err


def test_validate_accepts_blocked_with_reason():
    doc = TaskDoc(
        items=[TaskItem("s1", "登录", status="blocked", reason="需要验证码")]
    )
    assert doc.validate() is None


def test_validate_rejects_unknown_status():
    doc = TaskDoc(items=[TaskItem("s1", "A", status="doing")])
    err = doc.validate()
    assert err is not None
    assert "doing" in err


def test_validate_rejects_too_many_facts():
    doc = TaskDoc(facts=[f"fact {i}" for i in range(11)])
    err = doc.validate()
    assert err is not None
    assert "10" in err


def test_validate_rejects_overlong_fact():
    doc = TaskDoc(facts=["x" * 121])
    err = doc.validate()
    assert err is not None
    assert "120" in err


# --------------------------------------------------------------------------
# state-transition discipline (A4): compare against the previous doc
# --------------------------------------------------------------------------


def test_validate_rejects_pending_to_completed_jump():
    previous = TaskDoc(items=[TaskItem("s1", "打开设置", status="pending")])
    candidate = TaskDoc(
        items=[TaskItem("s1", "打开设置", status="completed", evidence_note="设置页可见")]
    )
    err = candidate.validate(previous=previous)
    assert err is not None
    assert "in_progress" in err
    assert "s1" in err


def test_validate_allows_in_progress_to_completed():
    previous = TaskDoc(items=[TaskItem("s1", "打开设置", status="in_progress")])
    candidate = TaskDoc(
        items=[TaskItem("s1", "打开设置", status="completed", evidence_note="设置页可见")]
    )
    assert candidate.validate(previous=previous) is None


def test_validate_rejects_batch_pending_to_completed():
    previous = TaskDoc(
        items=[
            TaskItem("s1", "A", status="pending"),
            TaskItem("s2", "B", status="pending"),
        ]
    )
    candidate = TaskDoc(
        items=[
            TaskItem("s1", "A", status="completed", evidence_note="proofA"),
            TaskItem("s2", "B", status="completed", evidence_note="proofB"),
        ]
    )
    err = candidate.validate(previous=previous)
    assert err is not None
    assert "批量" in err


def test_validate_allows_new_item_added_completed():
    # A brand-new item (not in previous) added already-completed is not a "jump":
    # it never existed as pending, so only the structural evidence gate applies.
    previous = TaskDoc(items=[TaskItem("s1", "A", status="in_progress")])
    candidate = TaskDoc(
        items=[
            TaskItem("s1", "A", status="in_progress"),
            TaskItem("s2", "B", status="completed", evidence_note="proofB"),
        ]
    )
    assert candidate.validate(previous=previous) is None


def test_validate_no_previous_skips_transition_checks():
    # Backward compat: validate() with no previous doc only does structural checks
    # (a completed item with evidence is fine even though there's no prior state).
    candidate = TaskDoc(
        items=[TaskItem("s1", "A", status="completed", evidence_note="proof")]
    )
    assert candidate.validate() is None



# --------------------------------------------------------------------------
# open-item queries
# --------------------------------------------------------------------------


def test_has_open_items_true_for_pending_and_in_progress():
    assert TaskDoc(items=[TaskItem("s1", "A", status="pending")]).has_open_items()
    assert TaskDoc(items=[TaskItem("s1", "A", status="in_progress")]).has_open_items()


def test_has_open_items_false_when_all_terminal():
    doc = TaskDoc(
        items=[
            TaskItem("s1", "A", status="completed"),
            TaskItem("s2", "B", status="blocked", reason="卡住"),
        ]
    )
    assert not doc.has_open_items()


def test_open_items_summary_lists_only_open():
    doc = TaskDoc(
        items=[
            TaskItem("s1", "已完成", status="completed"),
            TaskItem("s2", "进行中", status="in_progress"),
            TaskItem("s3", "待办", status="pending"),
        ]
    )
    summary = doc.open_items_summary()
    assert "s2" in summary and "s3" in summary
    assert "s1" not in summary


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def test_render_empty_doc_returns_empty_string():
    assert TaskDoc().render() == ""


def test_render_contains_three_sections():
    doc = TaskDoc(
        goal_base="连上 WLAN",
        amendments=["用户补充：优先 5G"],
        items=[TaskItem("s1", "打开设置", status="in_progress")],
        facts=["WiFi 名: HomeNet"],
    )
    out = doc.render("cn")
    assert "## 目标" in out
    assert "base: 连上 WLAN" in out
    assert "用户补充：优先 5G" in out
    assert "## 路线" in out
    assert "[in_progress] s1: 打开设置" in out
    assert "## 关键事实" in out
    assert "WiFi 名: HomeNet" in out


def test_render_omits_empty_sections():
    # only goal_base -> no 路线 / 关键事实 headers
    doc = TaskDoc(goal_base="仅目标")
    out = doc.render("cn")
    assert "## 目标" in out
    assert "## 路线" not in out
    assert "## 关键事实" not in out


def test_render_blocked_item_shows_reason():
    doc = TaskDoc(items=[TaskItem("s1", "登录", status="blocked", reason="需验证码")])
    out = doc.render("cn")
    assert "需验证码" in out


def test_render_en_uses_english_headers():
    doc = TaskDoc(goal_base="connect wifi", items=[TaskItem("s1", "open settings")])
    out = doc.render("en")
    assert "## Goal" in out
    assert "## Plan" in out
    assert "## 目标" not in out


# --------------------------------------------------------------------------
# update_task_doc tool
# --------------------------------------------------------------------------


def _tool(session, lang="cn"):
    return make_update_task_doc_tool(session, lang)


def test_tool_items_full_replace():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke({"items": [{"id": "s1", "content": "A", "status": "pending"}]})
    tool.invoke({"items": [{"id": "s2", "content": "B", "status": "in_progress"}]})
    assert [i.id for i in session.task_doc.items] == ["s2"]
    assert session.task_doc.items[0].status == "in_progress"


def test_tool_amendments_append_not_rewrite():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke({"add_amendments": ["细化1"]})
    tool.invoke({"add_amendments": ["细化2"]})
    assert session.task_doc.amendments == ["细化1", "细化2"]


def test_tool_facts_full_replace():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke({"facts": ["价格 99"]})
    tool.invoke({"facts": ["价格 88", "库存 3"]})
    assert session.task_doc.facts == ["价格 88", "库存 3"]


def test_tool_preserves_goal_base_across_writes():
    session = FakePhoneSession({})
    session.screen_seq = 1
    session.task_doc = TaskDoc(goal_base="原始任务")
    tool = _tool(session)
    tool.invoke({"items": [{"id": "s1", "content": "A"}]})
    assert session.task_doc.goal_base == "原始任务"


def test_tool_success_returns_render():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    out = tool.invoke({"items": [{"id": "s1", "content": "打开设置"}]})
    assert "已更新任务板" in out
    assert "## 路线" in out
    assert "打开设置" in out


def test_tool_screen_seq_zero_appends_observe_hint():
    session = FakePhoneSession({})
    assert session.screen_seq == 0
    tool = _tool(session)
    out = tool.invoke({"items": [{"id": "s1", "content": "A"}]})
    assert "read_screen" in out
    # write still committed
    assert session.task_doc.items[0].id == "s1"


def test_tool_no_hint_after_observation():
    session = FakePhoneSession({})
    session.screen_seq = 2
    tool = _tool(session)
    out = tool.invoke({"items": [{"id": "s1", "content": "A"}]})
    assert "read_screen" not in out


def test_tool_validation_failure_does_not_write():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    out = tool.invoke(
        {
            "items": [
                {"id": "s1", "content": "A", "status": "in_progress"},
                {"id": "s2", "content": "B", "status": "in_progress"},
            ]
        }
    )
    assert out.startswith("未写入")
    # nothing committed
    assert getattr(session, "task_doc", None) is None or session.task_doc.items == []


def test_tool_blocked_without_reason_rejected_no_write():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    out = tool.invoke({"items": [{"id": "s1", "content": "登录", "status": "blocked"}]})
    assert out.startswith("未写入")
    assert getattr(session, "task_doc", None) is None or session.task_doc.items == []


def test_tool_rejects_pending_to_completed_jump_no_write():
    # A4: the tool validates against the pre-write board, so a pending item cannot
    # be marked completed in one write (must pass through in_progress first).
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke({"items": [{"id": "s1", "content": "打开设置", "status": "pending"}]})
    out = tool.invoke(
        {"items": [{"id": "s1", "content": "打开设置", "status": "completed", "evidence_note": "设置页可见"}]}
    )
    assert out.startswith("未写入")
    # The board still holds the original pending item (write rejected).
    assert session.task_doc.items[0].status == "pending"


def test_tool_allows_in_progress_then_completed():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke({"items": [{"id": "s1", "content": "打开设置", "status": "pending"}]})
    tool.invoke({"items": [{"id": "s1", "content": "打开设置", "status": "in_progress"}]})
    out = tool.invoke(
        {"items": [{"id": "s1", "content": "打开设置", "status": "completed", "evidence_note": "设置页可见"}]}
    )
    assert "已更新任务板" in out
    assert session.task_doc.items[0].status == "completed"


def test_tool_rejects_batch_pending_to_completed_no_write():
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    tool.invoke(
        {
            "items": [
                {"id": "s1", "content": "A", "status": "pending"},
                {"id": "s2", "content": "B", "status": "pending"},
            ]
        }
    )
    out = tool.invoke(
        {
            "items": [
                {"id": "s1", "content": "A", "status": "completed", "evidence_note": "pa"},
                {"id": "s2", "content": "B", "status": "completed", "evidence_note": "pb"},
            ]
        }
    )
    assert out.startswith("未写入")
    assert [i.status for i in session.task_doc.items] == ["pending", "pending"]


def test_tool_bad_item_shape_rejected_no_write():
    # A non-dict item is fail-closed: pydantic rejects it at the schema layer
    # (raises) or the body coercion guard returns "未写入" — either way nothing
    # is committed to the session.
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    try:
        out = tool.invoke({"items": ["not-a-dict"]})
        assert out.startswith("未写入")
    except Exception:  # noqa: BLE001 - schema-layer ValidationError is also fail-closed
        pass
    assert getattr(session, "task_doc", None) is None or session.task_doc.items == []


def test_tool_bad_item_shape_direct_call_returns_error():
    # Exercise the body-level coercion guard directly (bypassing the pydantic
    # schema) to prove it returns an error string rather than raising.
    session = FakePhoneSession({})
    session.screen_seq = 1
    tool = _tool(session)
    out = tool.func(items=["not-a-dict"])
    assert out.startswith("未写入")
    assert getattr(session, "task_doc", None) is None or session.task_doc.items == []


def test_tool_does_not_accept_goal_base_param():
    tool = _tool(FakePhoneSession({}))
    assert "goal_base" not in tool.args


# --------------------------------------------------------------------------
# registration in build_tools
# --------------------------------------------------------------------------


def test_build_tools_registers_update_task_doc_by_default():
    session = FakePhoneSession({})
    names = {t.name for t in build_tools(session, FakeConfig())}
    assert "update_task_doc" in names
    # finish path unaffected
    assert "finish" in names


def test_build_tools_omits_update_task_doc_when_disabled():
    session = FakePhoneSession({})

    class Cfg(FakeConfig):
        taskdoc_enabled = False

    names = {t.name for t in build_tools(session, Cfg())}
    assert "update_task_doc" not in names
    assert "finish" in names
