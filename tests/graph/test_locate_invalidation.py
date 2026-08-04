"""S4: defensive loop — invalidated locate_* marks.

A ``locate_*`` mark that was tapped and clearly did not take effect is
invalidated: it is excluded from marks_block, rejected by grounding (tap
targets and locate scope), and refused by the execute-side scope validation.
Accessibility-origin marks are never invalidated (structural trust), and the
registry's D2 inheritance/version semantics are untouched (filter at the
render/grounding boundaries only).
"""

import pytest

from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.graph.marks import Mark, MarkRegistry, compute_raw_screenshot_hash
from phone_agent.graph.nodes.plan import _marks_summary
from phone_agent.graph.nodes.reflect import _newly_invalidated_locate_marks
from phone_agent.graph.tools.locate import locate_target
from phone_agent.grounding.fake import FakeGroundingProvider


_SCREEN = "screen-1"
_RAW = "fake-image"
_RAW_HASH = compute_raw_screenshot_hash(_RAW)


def _registry() -> MarkRegistry:
    return MarkRegistry(
        screen_id=_SCREEN,
        marks={
            "m1": Mark(
                mark_id="m1",
                screen_id=_SCREEN,
                bbox=(0, 0, 100, 100),
                center=(50, 50),
                source="accessibility",
                role="TextView",
                text_summary="首页",
            ),
            "locate_1": Mark(
                mark_id="locate_1",
                screen_id=_SCREEN,
                bbox=(400, 400, 600, 600),
                center=(500, 500),
                source="locateanything_mlx",
                confidence=1.0,
            ),
            "locate_2": Mark(
                mark_id="locate_2",
                screen_id=_SCREEN,
                bbox=(100, 100, 200, 200),
                center=(150, 150),
                source="locateanything_mlx",
                confidence=1.0,
            ),
        },
        semantic_screen_id="semantic-1",
        observation_epoch=1,
        raw_screenshot_hash=_RAW_HASH,
    )


# ----------------------------------------------------------------------
# marks_block rendering
# ----------------------------------------------------------------------


def test_prompt_block_excludes_invalidated_marks() -> None:
    registry = _registry()
    block = registry.prompt_block("cn", excluded_mark_ids=["locate_1"])

    assert "locate_1" not in block
    assert "m1" in block
    assert "locate_2" in block


def test_prompt_block_renders_empty_when_all_marks_invalidated() -> None:
    registry = _registry()
    block = registry.prompt_block(
        "cn", excluded_mark_ids=["locate_1", "locate_2", "m1"]
    )
    assert block == ""


def test_prompt_block_unfiltered_unchanged_without_invalidations() -> None:
    registry = _registry()
    assert "locate_1" in registry.prompt_block("cn")


def test_marks_summary_excludes_invalidated_marks() -> None:
    summary = _marks_summary(_registry(), excluded_mark_ids=["locate_1"])
    assert "locate_1" not in summary
    assert "m1" in summary
    assert "locate_2" in summary


# ----------------------------------------------------------------------
# Grounding: tap targets and locate scope
# ----------------------------------------------------------------------


def test_grounding_rejects_tap_on_invalidated_locate_mark() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "Tap", "target_mark_id": "locate_1"},
            mark_registry=_registry(),
            screen_id=_SCREEN,
            invalidated_mark_ids=["locate_1"],
        )
    assert exc_info.value.code == "mark_invalidated"


def test_grounding_ax_mark_unaffected_by_locate_invalidation() -> None:
    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "Tap", "target_mark_id": "m1"},
        mark_registry=_registry(),
        screen_id=_SCREEN,
        invalidated_mark_ids=["locate_1"],
    )
    assert action["action"] == "Tap"
    assert action["element"] == [50, 50]


def test_grounding_rejects_locate_scope_on_invalidated_mark() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月2日",
                "scope_mark_id": "locate_1",
            },
            mark_registry=_registry(),
            screen_id=_SCREEN,
            invalidated_mark_ids=["locate_1"],
        )
    assert exc_info.value.code == "mark_invalidated"


# ----------------------------------------------------------------------
# execute-side scope validation (defense in depth)
# ----------------------------------------------------------------------


def test_execute_rejects_invalidated_scope_fail_closed(base_state) -> None:
    provider = FakeGroundingProvider(bbox=[100, 100, 200, 200])

    class _Device:
        def get_screenshot(self, device_id=None):
            return type("Shot", (), {"base64_data": _RAW, "width": 1000, "height": 2000})()

    state = dict(base_state)
    state["screen_width"] = 1000
    state["screen_height"] = 2000
    state["mark_registry"] = _registry().to_dict()
    state["locate_count"] = 0
    state["invalidated_mark_ids"] = ["locate_1"]
    state["action_parsed"] = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月2日",
        "scope_mark_id": "locate_1",
    }
    outcome = locate_target(
        state, {"configurable": {"device_factory": _Device(), "locate_provider": provider}}
    )
    assert outcome.success is False
    assert outcome.failure_code == "scope_mark_invalidated"
    assert provider.requests == []


# ----------------------------------------------------------------------
# Reflect-side decision helper (unit; the full reflect node needs a model)
# ----------------------------------------------------------------------


def _tap_state(target_mark_id: str, action: str = "Tap") -> dict:
    return {
        "action_parsed": {"_metadata": "do", "action": action, "element": [500, 500]},
        "grounding_observation": {"provider": "mark_registry", "target": {"mark_id": target_mark_id}},
    }


def test_reflect_invalidates_failed_locate_tap() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("locate_1"), verdict="failed", failure_cause="coordinate_or_tap_offset"
    ) == ["locate_1"]


def test_reflect_invalidates_partial_with_tap_offset() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("locate_1"), verdict="partial", failure_cause="coordinate_or_tap_offset"
    ) == ["locate_1"]


def test_reflect_partial_without_offset_keeps_mark() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("locate_1"), verdict="partial", failure_cause="network_or_loading"
    ) == []


def test_reflect_succeeded_keeps_mark() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("locate_1"), verdict="succeeded", failure_cause=None
    ) == []


def test_reflect_never_invalidates_ax_mark() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("m1"), verdict="failed", failure_cause="coordinate_or_tap_offset"
    ) == []


def test_reflect_ignores_non_tap_actions() -> None:
    assert _newly_invalidated_locate_marks(
        _tap_state("locate_1", action="Swipe"), verdict="failed", failure_cause=None
    ) == []


def test_reflect_ignores_missing_grounding_target() -> None:
    state = {"action_parsed": {"_metadata": "do", "action": "Tap", "element": [1, 1]}}
    assert _newly_invalidated_locate_marks(state, verdict="failed", failure_cause=None) == []
