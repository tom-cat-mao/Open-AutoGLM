"""H2 Fix D: verifier before/after hash comparison uses the same algorithm.

The old code compared ``before_state["screen_hash"]`` (raw sha256 of the
screenshot) against the after side's ``build_screen_id`` (semantic identity) —
structurally unequal on every step, so ``screen_changed`` was always true and
``content_shifted`` expectations were permanently unknown. After the fix the
before side prefers ``screen_id`` (the plan frame's ``build_screen_id``
output), so the comparison is like-for-like; the raw hash remains only as a
fallback for states that never wrote a top-level ``screen_id``.
"""

from __future__ import annotations

from types import SimpleNamespace

from phone_agent.graph.marks import build_screen_id
from phone_agent.graph.verifier import verify_action_outcome

APP = "FakeApp"
B64 = "dGVzdA=="  # not a decodable image → deterministic perceptual-hash fallback
WIDTH, HEIGHT = 1000, 2000


def _after_screenshot() -> SimpleNamespace:
    return SimpleNamespace(
        base64_data=B64,
        width=WIDTH,
        height=HEIGHT,
    )


def _screen_id(*, current_app: str = APP) -> str:
    return build_screen_id(
        current_app=current_app,
        screenshot_b64=B64,
        width=WIDTH,
        height=HEIGHT,
    )


def test_verifier_same_semantic_screen_no_screen_changed_signal() -> None:
    """Same screen_id on both sides → screen_changed must NOT fire.

    Regression lock for the old raw-vs-semantic mismatch: with the raw hash
    differing from build_screen_id by construction, this previously reported
    screen_changed=True on every step of the same physical screen.
    """

    sid = _screen_id()
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            # Explicit generic expectation so the hash path is reached (Wait's
            # default loading_finished would return before the comparison).
            "expected_outcome": {"kind": "generic"},
            # screen_id matches the after frame (same algorithm); the raw hash
            # deliberately differs and must be ignored.
            "screen_id": sid,
            "screen_hash": "0" * 16,
        },
        after_screenshot=_after_screenshot(),
        after_app=APP,
        action_result={"success": True},
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is not True


def test_verifier_semantic_page_change_still_detects_screen_changed() -> None:
    """A real semantic change (different app → different screen_id) still fires.

    This is the counterpart guard: aligning the algorithms must not blind the
    verifier to genuine page transitions.
    """

    before = _screen_id()
    after = _screen_id(current_app="OtherApp")
    assert before != after

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {"kind": "generic"},
            "screen_id": before,
            "screen_hash": before,
        },
        after_screenshot=_after_screenshot(),
        after_app="OtherApp",
        action_result={"success": True},
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is True
    assert result.signals.get("screen_changed") is True


def test_verifier_content_shifted_same_screen_loses_noise_screen_changed() -> None:
    """content_shifted on an unchanged screen no longer short-circuits to
    ``content_shift_unverified`` via the always-true screen_changed signal.

    The expectation is still unknown (fail-closed), but the weak signal no
    longer claims a screen change that did not happen, so the reflect prompt
    stops being injected with per-step system noise.
    """

    sid = _screen_id()
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Swipe"},
            "screen_id": sid,
            "expected_outcome": {
                "kind": "content_shifted",
                "must_observe": [],
                "must_not_observe": [],
            },
        },
        after_screenshot=_after_screenshot(),
        after_app=APP,
        action_result={"success": True},
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is not True
    assert "content_shift_unverified" not in result.evidence.get(
        "missing_postconditions", []
    )


def test_verifier_raw_hash_fallback_keeps_old_behavior_when_no_screen_id() -> None:
    """States that never wrote a top-level screen_id still compare the raw hash
    (fallback path), preserving pre-fix behavior for legacy callers.
    """

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {"kind": "generic"},
            "screen_hash": "0000000000000001",
        },
        after_screenshot=_after_screenshot(),
        after_app=APP,
        action_result={"success": True},
    )

    after_hash = build_screen_id(
        current_app=APP, screenshot_b64=B64, width=WIDTH, height=HEIGHT
    )
    assert "0000000000000001" != after_hash
    assert result.evidence["weak_signals"].get("screen_changed") is True
