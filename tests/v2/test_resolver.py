"""Tests for phone_agent.v2.resolver (§8 + §12)."""

from __future__ import annotations

import pytest

from phone_agent.v2.resolver import (
    ResolveAmbiguousError,
    resolve_description,
)

from tests.v2._doubles import FakePhoneSession, make_mark


def test_exact_match_wins_over_substring():
    marks = {
        "ax_1": make_mark("ax_1", text="WLAN", role="TextView"),
        "ax_2": make_mark("ax_2", text="WLAN 设置", role="TextView"),
    }
    session = FakePhoneSession(marks)
    result = resolve_description(session, "WLAN")
    assert result.mark_id == "ax_1"


def test_role_exact_match():
    marks = {"ax_1": make_mark("ax_1", text="确定", role="Button")}
    session = FakePhoneSession(marks)
    result = resolve_description(session, "Button")
    assert result.mark_id == "ax_1"


def test_substring_match():
    marks = {
        "ax_1": make_mark("ax_1", text="打开蓝牙设置", role="TextView"),
        "ax_2": make_mark("ax_2", text="音量", role="TextView"),
    }
    session = FakePhoneSession(marks)
    result = resolve_description(session, "蓝牙")
    assert result.mark_id == "ax_1"


def test_normalized_fuzzy_match():
    marks = {"ax_1": make_mark("ax_1", text="Wi Fi Settings", role="TextView")}
    session = FakePhoneSession(marks)
    # whitespace + case differences resolve via normalized fuzzy tier
    result = resolve_description(session, "wifisettings")
    assert result.mark_id == "ax_1"


def test_zero_hits_falls_back_to_locate():
    located = make_mark("loc_1", text="隐藏目标", role="ImageView")
    session = FakePhoneSession({}, locate_result=located)
    result = resolve_description(session, "some invisible target")
    assert result.mark_id == "loc_1"
    # locate registered the mark on the session
    assert "loc_1" in session.marks


def test_multiple_hits_fail_closed():
    marks = {
        "ax_1": make_mark("ax_1", text="设置", role="TextView"),
        "ax_2": make_mark("ax_2", text="设置", role="TextView"),
    }
    session = FakePhoneSession(marks)
    with pytest.raises(ResolveAmbiguousError) as exc:
        resolve_description(session, "设置")
    assert len(exc.value.candidates) == 2
    # device was never touched (resolver does not execute)
    assert session.device_factory.calls == []


def test_ambiguous_candidates_capped_at_five():
    marks = {
        f"ax_{i}": make_mark(f"ax_{i}", text="项目", role="TextView")
        for i in range(8)
    }
    session = FakePhoneSession(marks)
    with pytest.raises(ResolveAmbiguousError) as exc:
        resolve_description(session, "项目")
    assert len(exc.value.candidates) == 5
