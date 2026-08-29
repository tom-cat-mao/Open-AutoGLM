"""Main-frame selection logic: follow latest unless a frame is pinned."""

from __future__ import annotations

from phone_agent.web.app import _choose_frame, _pin_toggle


def _screens(*seqs: int) -> list[dict]:
    return [{"seq": n, "app": "设置", "image": f"data:image/png;base64,f{n}"} for n in seqs]


def test_follows_latest_by_default():
    selected = {"seq": None, "pinned": False}
    shown = _choose_frame(_screens(1, 2), selected)
    assert shown["seq"] == 2
    # A newer frame arrives: still following.
    selected2 = {"seq": selected["seq"], "pinned": selected["pinned"]}
    shown = _choose_frame(_screens(1, 2, 3), selected2)
    assert shown["seq"] == 3


def test_pin_holds_and_toggle_releases():
    selected = {"seq": None, "pinned": False}
    screens = _screens(1, 2, 3)
    _choose_frame(screens, selected)
    _pin_toggle(selected, 1)
    shown = _choose_frame(screens, selected)
    assert shown["seq"] == 1  # pinned on the historical frame
    _pin_toggle(selected, 1)  # click again -> release
    shown = _choose_frame(screens, selected)
    assert shown["seq"] == 3  # following latest again


def test_pinned_frame_rolling_off_releases_pin():
    selected = {"seq": 1, "pinned": True}
    shown = _choose_frame(_screens(2, 3), selected)
    assert selected["pinned"] is False
    assert shown["seq"] == 3


def test_empty_history():
    selected = {"seq": None, "pinned": False}
    assert _choose_frame([], selected) is None
