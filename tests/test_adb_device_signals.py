from __future__ import annotations

import subprocess

from phone_agent.adb import device


class FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_get_focused_window_prefers_current_focus(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout=(
                "mFocusedApp=ActivityRecord{abc tv.danmaku.bili/.MainActivity}\n"
                "mCurrentFocus=Window{def u0 tv.danmaku.bili/com.bilibili.search2.main.BiliMainSearchActivity}\n"
            )
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    focused = device.get_focused_window_or_app()
    top_activity = device.get_top_activity()

    assert focused == "tv.danmaku.bili/com.bilibili.search2.main.BiliMainSearchActivity"
    assert (
        top_activity
        == "tv.danmaku.bili/com.bilibili.search2.main.BiliMainSearchActivity"
    )


def test_get_focused_window_skips_null_current_focus(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout=(
                "mCurrentFocus=null\n"
                "mFocusedApp=ActivityRecord{abc tv.danmaku.bili/.MainActivity}\n"
            )
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.get_focused_window_or_app() == "tv.danmaku.bili/.MainActivity"
    assert device.get_top_activity() == "tv.danmaku.bili/.MainActivity"


def test_is_keyboard_visible_from_input_method(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout="mShowRequested=true mInputShown=true mWindowVisible=false\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.is_keyboard_visible() is True


def test_is_keyboard_visible_false_for_zero_ime_window(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout="mInputShown=false mWindowVisible=false mImeWindowVis=0x0\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.is_keyboard_visible() is False


def test_is_keyboard_visible_ignores_nonzero_ime_window_without_shown(
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout="mInputShown=false mWindowVisible=false mImeWindowVis=0x1\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.is_keyboard_visible() is False


def test_installed_inventory_is_an_observed_package_fact(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout="package:com.android.chrome\npackage:com.example.unknown\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    inventory = device.get_installed_app_inventory("serial")

    assert inventory.device_id == "serial"
    assert inventory.packages == frozenset(
        {"com.android.chrome", "com.example.unknown"}
    )


def test_dump_uiautomator_xml_ignores_stderr_xml(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return FakeCompletedProcess(
            stdout="",
            stderr="<?xml version='1.0'?><hierarchy></hierarchy>",
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        device.dump_uiautomator_xml()
    except ValueError as exc:
        assert str(exc) == "No UiAutomator XML output"
    else:
        raise AssertionError("stderr XML should not be accepted as UiAutomator stdout")
