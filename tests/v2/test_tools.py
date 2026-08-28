"""Tests for phone_agent.v2.tools (§7 + §12)."""

from __future__ import annotations

from phone_agent.v2.resolver import LocateAmbiguousError
from phone_agent.v2.tools import build_tools

from tests.v2._doubles import FakeConfig, FakePhoneSession, make_mark


def _tool_map(session, config=None):
    tools = build_tools(session, config or FakeConfig())
    return {t.name: t for t in tools}


def _text(out) -> str:
    """Join the text blocks of a tool result.

    Success paths return a multimodal ``list[dict]`` (text + optional image);
    error/control paths return a plain ``str``. This helper normalises both to
    the concatenated text so assertions read uniformly.
    """

    if isinstance(out, str):
        return out
    return "\n".join(
        b.get("text", "")
        for b in out
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _image_blocks(out) -> list[dict]:
    if not isinstance(out, list):
        return []
    return [
        b
        for b in out
        if isinstance(b, dict) and b.get("type") in {"image_url", "image"}
    ]


def test_full_tool_set_present():
    session = FakePhoneSession()
    names = set(_tool_map(session))
    assert names == {
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
    }


def test_tap_mark_id_coordinate_conversion():
    # center 0-1000 rel; 1080x2400 device -> x=int(500/1000*1080)=540, y=int(300/1000*2400)=720
    marks = {"ax_1": make_mark("ax_1", text="WLAN", role="TextView", center=(500, 300))}
    session = FakePhoneSession(marks, width=1080, height=2400)
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_mark_id": "ax_1"})
    assert ("tap", 540, 720) in session.device_factory.calls
    assert _text(out).startswith("OK.")
    assert "[OBS]" in _text(out)


def test_tap_success_returns_multimodal_with_image():
    marks = {"ax_1": make_mark("ax_1", text="WLAN", center=(500, 300))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_mark_id": "ax_1"})
    # Success is a content list: first block is the OK text, an image block follows.
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert out[0]["text"].startswith("OK.")
    imgs = _image_blocks(out)
    assert len(imgs) == 1
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert imgs[0]["screen_seq"] == session.screen_seq


def test_tap_appends_observation_block():
    marks = {"ax_1": make_mark("ax_1", text="X", center=(100, 100))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_mark_id": "ax_1"})
    assert "[OBS] app=com.example.app screen#1" in _text(out)
    assert session.observe_count == 1


def test_tap_stale_mark_hint_no_execution():
    session = FakePhoneSession({})  # no marks -> stale
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_mark_id": "ax_missing"})
    assert isinstance(out, str)  # error branch stays str (no image)
    assert "stale mark" in out
    assert session.device_factory.calls == []


def test_tap_ambiguous_description_no_execution():
    marks = {
        "ax_1": make_mark("ax_1", text="设置", role="TextView"),
        "ax_2": make_mark("ax_2", text="设置", role="TextView"),
    }
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_description": "设置"})
    assert isinstance(out, str)
    assert out.startswith("ambiguous:")
    assert session.device_factory.calls == []


def test_tap_description_resolves_and_taps():
    marks = {"ax_1": make_mark("ax_1", text="确认付款", center=(200, 400))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke({"target_description": "确认付款"})
    assert _text(out).startswith("OK.")
    assert any(c[0] == "tap" for c in session.device_factory.calls)


def test_tap_both_addresses_rejected():
    marks = {"ax_1": make_mark("ax_1", text="X")}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["tap"].invoke(
        {"target_mark_id": "ax_1", "target_description": "X"}
    )
    assert isinstance(out, str)
    assert out.startswith("error:")
    assert session.device_factory.calls == []


def test_tap_no_address_rejected():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["tap"].invoke({})
    assert isinstance(out, str)
    assert out.startswith("error:")


def test_long_press_uses_long_press_call():
    marks = {"ax_1": make_mark("ax_1", text="图标", center=(500, 500))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    tools["long_press"].invoke({"target_mark_id": "ax_1"})
    assert any(c[0] == "long_press" for c in session.device_factory.calls)


def test_type_text_focuses_and_restores_keyboard():
    marks = {"ax_1": make_mark("ax_1", text="搜索框", center=(500, 100))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["type_text"].invoke(
        {"text": "hello", "target_mark_id": "ax_1"}
    )
    kinds = [c[0] for c in session.device_factory.calls]
    assert "tap" in kinds  # focus tap
    assert "detect_kbd" in kinds
    assert ("type_text", "hello") in session.device_factory.calls
    assert "restore_kbd" in kinds
    assert _text(out).startswith("OK.")


def test_type_text_without_target_skips_focus_tap():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    tools["type_text"].invoke({"text": "abc"})
    kinds = [c[0] for c in session.device_factory.calls]
    assert "tap" not in kinds
    assert ("type_text", "abc") in session.device_factory.calls


def test_scroll_swipes_midscreen():
    session = FakePhoneSession({}, width=1080, height=2400)
    tools = _tool_map(session)
    out = tools["scroll"].invoke({"direction": "down"})
    swipes = [c for c in session.device_factory.calls if c[0] == "swipe"]
    assert swipes
    assert _text(out).startswith("OK. scroll down")


def test_swipe_relative_to_absolute():
    session = FakePhoneSession({}, width=1000, height=1000)
    tools = _tool_map(session)
    tools["swipe"].invoke({"start": [100, 200], "end": [300, 400]})
    assert ("swipe", 100, 200, 300, 400) in session.device_factory.calls


def test_swipe_bad_input_no_execution():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["swipe"].invoke({"start": [1], "end": [2, 3]})
    assert isinstance(out, str)
    assert out.startswith("error:")
    assert session.device_factory.calls == []


def test_back_home_wait():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    assert _text(tools["back"].invoke({})).startswith("OK. back")
    assert _text(tools["home"].invoke({})).startswith("OK. home")
    assert _text(tools["wait"].invoke({"seconds": 0})).startswith("OK. waited")
    kinds = [c[0] for c in session.device_factory.calls]
    assert "back" in kinds and "home" in kinds


def test_launch_app_known_executes():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["launch_app"].invoke({"app_name": "微信"})
    assert _text(out).startswith("OK. launched")
    assert session.device_factory.launched == ["微信"]


def test_launch_app_unknown_not_executed():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["launch_app"].invoke({"app_name": "NoSuchApp_zzz_123"})
    assert isinstance(out, str)
    assert out.startswith("unknown app")
    assert session.device_factory.launched == []


def test_read_screen_observes():
    marks = {"ax_1": make_mark("ax_1", text="WLAN", center=(500, 300))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    out = tools["read_screen"].invoke({})
    assert "[OBS]" in _text(out)
    assert "ax_1" in _text(out)
    assert session.observe_count == 1
    # read_screen success carries an image on a fresh screen.
    assert len(_image_blocks(out)) == 1


def test_locate_success_registers_mark():
    located = make_mark("loc_9", text="隐藏按钮", role="ImageView")
    session = FakePhoneSession({}, locate_result=located)
    tools = _tool_map(session)
    out = tools["locate"].invoke({"description": "隐藏按钮"})
    assert "已定位并注册为 mark loc_9" in out
    assert "loc_9" in session.marks


def test_locate_failure_registers_nothing():
    session = FakePhoneSession(
        {}, locate_error=LocateAmbiguousError("no candidate")
    )
    tools = _tool_map(session)
    out = tools["locate"].invoke({"description": "ghost"})
    assert out.startswith("未定位")
    assert session.marks == {}


def test_finish_empty_evidence_rejected():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["finish"].invoke({"summary": "done", "evidence": []})
    assert out.startswith("error:")
    assert session.finished is False


def test_finish_two_step_review_then_confirm():
    # First call returns the review packet and does NOT land; confirm lands.
    session = FakePhoneSession({})
    tools = _tool_map(session)
    first = tools["finish"].invoke(
        {"summary": "已连上 WLAN", "evidence": ["WLAN 显示已连接"]}
    )
    assert "[FINISH 复核包]" in first
    assert session.finished is False

    second = tools["finish"].invoke(
        {"summary": "已连上 WLAN", "evidence": ["WLAN 显示已连接"], "confirm": True}
    )
    assert second == "已确认完成"
    assert session.finished is True
    assert session.finish_summary == "已连上 WLAN"


def test_finish_stale_confirm_reissues_packet():
    # An observation between the packet and the confirm invalidates the review;
    # the stale confirm re-emits a fresh packet instead of landing.
    session = FakePhoneSession({})
    tools = _tool_map(session)
    tools["finish"].invoke({"summary": "x", "evidence": ["proof"]})
    # A read_screen bumps screen_seq, making the recorded review_seq stale.
    tools["read_screen"].invoke({})
    out = tools["finish"].invoke(
        {"summary": "x", "evidence": ["proof"], "confirm": True}
    )
    assert "[FINISH 复核包]" in out
    assert session.finished is False


def test_finish_off_mode_single_step_lands():
    # PHONE_AGENT_FINISH_VERIFY=off degrades to the pre-two-step single call.
    class OffConfig(FakeConfig):
        finish_verify = "off"

    session = FakePhoneSession({})
    tools = _tool_map(session, OffConfig())
    out = tools["finish"].invoke(
        {"summary": "已连上 WLAN", "evidence": ["WLAN 显示已连接"]}
    )
    assert out == "已记录完成声明"
    assert session.finished is True
    assert session.finish_summary == "已连上 WLAN"


def test_finish_whitespace_only_evidence_rejected():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["finish"].invoke({"summary": "x", "evidence": ["  ", ""]})
    assert out.startswith("error:")
    assert session.finished is False


def test_ask_user_formats_question():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["ask_user"].invoke({"question": "选择哪个账户?"})
    assert "选择哪个账户?" in out


def test_take_over_records_reason():
    session = FakePhoneSession({})
    tools = _tool_map(session)
    out = tools["take_over"].invoke({"reason": "需要登录"})
    assert "需要登录" in out
    assert session.takeover_reason == "需要登录"


def test_actuation_result_contains_obs_block_when_observe_works():
    marks = {"ax_1": make_mark("ax_1", text="X", center=(500, 500))}
    session = FakePhoneSession(marks)
    tools = _tool_map(session)
    for name, args in [
        ("tap", {"target_mark_id": "ax_1"}),
        ("back", {}),
        ("home", {}),
    ]:
        out = tools[name].invoke(args)
        assert "[OBS]" in _text(out)


def test_locate_success_returns_str_no_image():
    located = make_mark("loc_9", text="隐藏按钮", role="ImageView")
    session = FakePhoneSession({}, locate_result=located)
    tools = _tool_map(session)
    out = tools["locate"].invoke({"description": "隐藏按钮"})
    # locate does not produce a new screen -> str result, never an image.
    assert isinstance(out, str)
    assert _image_blocks(out) == []


def test_same_screen_dedup_drops_image_second_time():
    # A static screen keeps the same screenshot hash; the second observation
    # reuses the text OBS but drops the image and notes it.
    marks = {"ax_1": make_mark("ax_1", text="X", center=(500, 500))}
    session = FakePhoneSession(marks, static_screen=True)
    tools = _tool_map(session)

    first = tools["read_screen"].invoke({})
    assert len(_image_blocks(first)) == 1

    second = tools["read_screen"].invoke({})
    assert _image_blocks(second) == []
    assert "未重复发图" in _text(second)


def test_changed_screen_sends_new_image():
    # A dynamic screen bumps the payload each observe -> hash changes -> image
    # ships every step and last_image_hash tracks the newest.
    marks = {"ax_1": make_mark("ax_1", text="X", center=(500, 500))}
    session = FakePhoneSession(marks, static_screen=False)
    tools = _tool_map(session)

    first = tools["read_screen"].invoke({})
    second = tools["read_screen"].invoke({})
    assert len(_image_blocks(first)) == 1
    assert len(_image_blocks(second)) == 1
    assert "未重复发图" not in _text(second)


def test_observe_failure_returns_single_text_block_no_image():
    marks = {"ax_1": make_mark("ax_1", text="X", center=(500, 500))}
    session = FakePhoneSession(marks)
    session._observe_should_fail = True
    tools = _tool_map(session)
    # tap executes the device action, then re-observation fails -> content list
    # with only a text block (OK head + re-observation-failed note), no image.
    out = tools["tap"].invoke({"target_mark_id": "ax_1"})
    assert _image_blocks(out) == []
    assert "re-observation failed" in _text(out)
    assert any(c[0] == "tap" for c in session.device_factory.calls)
