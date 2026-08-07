"""H2 Fix D: verifier before/after hash comparison uses the same algorithm.

The old code compared ``before_state["screen_hash"]`` (raw sha256 of the
screenshot) against the after side's ``build_screen_id`` (semantic identity) —
structurally unequal on every step, so ``screen_changed`` was always true and
``content_shifted`` expectations were permanently unknown. After the fix the
before side prefers ``screen_id`` (the plan frame's ``build_screen_id``
output), and the after side passes the current frame's accessibility-origin
marks through the same topology projection, so the comparison is like-for-like
on real screens: same screen with unchanged marks → no ``screen_changed``;
page flip / app switch / marks-topology change → ``screen_changed``.

Every ``before_state["screen_id"]`` here is built with marks (as the plan
frame does in ``observation.build_observation``) — no mark-less before states.
"""

from __future__ import annotations

from types import SimpleNamespace

from phone_agent.graph.marks import build_screen_id
from phone_agent.graph.verifier import verify_action_outcome

APP = "FakeApp"
B64 = "dGVzdA=="  # not a decodable image → deterministic perceptual-hash fallback
WIDTH, HEIGHT = 1000, 2000

# Real accessibility-origin marks (the projection that feeds the screen_id
# topology digest in observation.build_observation).
AX_MARKS = [
    {
        "mark_id": "ax_1",
        "bbox": [10, 20, 30, 40],
        "role": "button",
        "source": "uiautomator",
    },
    {
        "mark_id": "ax_2",
        "bbox": [50, 60, 70, 80],
        "role": "text",
        "source": "accessibility_tree",
    },
]
# A provider/locate mark: never part of screen identity (D1).
LOCATE_MARK = {
    "mark_id": "locate_1",
    "bbox": [5, 5, 9, 9],
    "role": "icon",
    "source": "locateanything",
}


def _after_screenshot() -> SimpleNamespace:
    return SimpleNamespace(
        base64_data=B64,
        width=WIDTH,
        height=HEIGHT,
    )


def _screen_id(
    *, current_app: str = APP, marks: list[dict] | None = None
) -> str:
    return build_screen_id(
        current_app=current_app,
        screenshot_b64=B64,
        width=WIDTH,
        height=HEIGHT,
        marks=marks,
    )


def _after_observation(*, marks: list[dict]) -> dict:
    return {"marks": marks}


def test_verifier_same_semantic_screen_no_screen_changed_signal() -> None:
    """Same screen_id on both sides (same marks, same app) → screen_changed must
    NOT fire.

    The after side carries the same accessibility marks plus a locate mark;
    the locate mark must be projected out so the topology digest stays equal.
    This locks the fix where the after side previously built its hash without
    marks → digest was always ``sha256("")`` and every real screen read as
    changed.
    """

    sid = _screen_id(marks=AX_MARKS)
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            # Explicit generic expectation so the hash path is reached (Wait's
            # default loading_finished would return before the comparison).
            "expected_outcome": {"kind": "generic"},
            # screen_id matches the after frame (same algorithm + same marks);
            # the raw hash deliberately differs and must be ignored.
            "screen_id": sid,
            "screen_hash": "0" * 16,
        },
        after_screenshot=_after_screenshot(),
        after_app=APP,
        action_result={"success": True},
        after_observation=_after_observation(marks=[*AX_MARKS, LOCATE_MARK]),
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is not True


def test_verifier_semantic_page_change_still_detects_screen_changed() -> None:
    """A real semantic change (different app → different screen_id) still fires,
    even when the marks topology is identical on both sides.

    This is the counterpart guard: aligning the algorithms must not blind the
    verifier to genuine page transitions.
    """

    before = _screen_id(marks=AX_MARKS)
    after = _screen_id(current_app="OtherApp", marks=AX_MARKS)
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
        after_observation=_after_observation(marks=AX_MARKS),
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is True
    assert result.signals.get("screen_changed") is True


def test_verifier_marks_topology_change_detects_screen_changed() -> None:
    """Same app and screenshot but a changed marks topology (a mark's bbox
    moved) flips the screen_id → screen_changed fires.

    This is the scenario the old mark-less after side could never detect:
    the digest was pinned to ``sha256("")`` so any real marks change was
    invisible (and, worse, any real screen read as changed).
    """

    before = _screen_id(marks=AX_MARKS)
    moved_marks = [
        {**mark, "bbox": [11, 21, 31, 41]} if mark["mark_id"] == "ax_1" else mark
        for mark in AX_MARKS
    ]
    after = _screen_id(marks=moved_marks)
    assert before != after

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {"kind": "generic"},
            "screen_id": before,
            "screen_hash": before,
        },
        after_screenshot=_after_screenshot(),
        after_app=APP,
        action_result={"success": True},
        after_observation=_after_observation(marks=moved_marks),
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

    sid = _screen_id(marks=AX_MARKS)
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
        after_observation=_after_observation(marks=AX_MARKS),
    )

    assert result.status == "unknown"
    assert result.evidence["weak_signals"].get("screen_changed") is not True
    assert "content_shift_unverified" not in result.evidence.get(
        "missing_postconditions", []
    )
