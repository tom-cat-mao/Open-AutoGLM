from phone_agent.graph.verifier import (
    _match_expected_text,
    _selected_object_signals,
    verify_action_outcome,
)

# Regression fixtures from the 小红书 "Chaster 117" run: the tapped note card was a
# bare layout container with no text of its own, so its `text_summary` fell back to
# the Java class name. Every screen contains such a container, which made the
# `evidence_summary in text_blob` check a tautology.
CLASS_NAME_ONLY_EVIDENCE = "FrameLayout"
FEED_SURFACE = "com.xingin.xhs/com.xingin.alioth.search.GlobalSearchActivity"
DETAIL_SURFACE = "com.xingin.xhs/com.xingin.matrix.notedetail.NoteDetailActivity"


def test_expected_text_contains_is_case_insensitive_for_uppercase_input() -> None:
    matched, missing = _match_expected_text(["VillageThomas"], "result: villagethomas")

    assert matched == ["VillageThomas"]
    assert missing == []


def test_sensitive_expected_text_verifies_with_raw_contains() -> None:
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {
                "kind": "target_appeared",
                "must_observe": ["13800138000"],
            },
        },
        after_screenshot=object(),
        after_app="Settings",
        action_result={"success": True, "message": "ok"},
        after_observation={"marks": [{"text_summary": "contact 13800138000"}]},
    )

    assert result.status == "success"
    assert result.evidence["matched_postconditions"] == ["13800138000"]


def test_class_name_evidence_is_not_a_match() -> None:
    """A class-name-shaped evidence summary carries no content, so it cannot match.

    Every screen renders anonymous layout containers, so accepting a class name as
    evidence makes the containment check true regardless of what was tapped.
    """

    signals = _selected_object_signals(
        {
            "object_type": "control",
            "evidence_summary": CLASS_NAME_ONLY_EVIDENCE,
            "expected_page_type": "page_opened",
        },
        {"marks": [{"text_summary": CLASS_NAME_ONLY_EVIDENCE}]},
        CLASS_NAME_ONLY_EVIDENCE.casefold(),
        None,
    )

    assert signals["selected_object_text_match"] is False
    assert "selected_object_match" not in signals


def test_textless_target_does_not_reach_high_confidence_success() -> None:
    """A text-less target must not become conclusive selected-object evidence."""

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Tap", "element": [747, 418]},
            "expected_outcome": {
                "kind": "target_appeared",
                "object_type": "control",
                "evidence_summary": CLASS_NAME_ONLY_EVIDENCE,
                "expected_page_type": "page_opened",
            },
        },
        after_screenshot=object(),
        after_app="小红书",
        action_result={"success": True, "message": "ok"},
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
        after_observation={
            "marks": [{"text_summary": CLASS_NAME_ONLY_EVIDENCE}],
            "device_signals": {"top_activity": DETAIL_SURFACE},
        },
    )

    assert result.confidence < 0.9


def test_same_surface_after_tap_is_reported() -> None:
    """Tapping without leaving the surface is the 'detail never opened' case."""

    signals = _selected_object_signals(
        {
            "object_type": "card",
            "evidence_summary": "26年F1超车真的没意义了么？",
            "expected_page_type": "page_opened",
        },
        {
            "snapshot": {"foreground_activity": FEED_SURFACE},
            "device_signals": {"top_activity": FEED_SURFACE},
            "marks": [{"text_summary": "26年F1超车真的没意义了么？"}],
        },
        "26年f1超车真的没意义了么？",
        None,
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
    )

    assert signals["same_surface_still_visible"] is True


def test_surface_change_clears_same_surface_signal() -> None:
    """Opening the detail surface must not report 'still on the same surface'."""

    signals = _selected_object_signals(
        {
            "object_type": "card",
            "evidence_summary": "26年F1超车真的没意义了么？",
            "expected_page_type": "page_opened",
        },
        {
            "snapshot": {"foreground_activity": DETAIL_SURFACE},
            "device_signals": {"top_activity": DETAIL_SURFACE},
            "marks": [{"text_summary": "26年F1超车真的没意义了么？"}],
        },
        "26年f1超车真的没意义了么？",
        None,
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
    )

    assert not signals.get("same_surface_still_visible")
