"""S1: locate action optional ``scope_mark_id`` schema.

Covers adapter / validator / grounding validation for the optional scope
field: accepted when it references an existing mark, rejected fail-closed when
missing from the registry or malformed (P0 #8: only existing marks may be
referenced).
"""

import pytest

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.graph.marks import Mark, MarkRegistry


def _registry() -> MarkRegistry:
    return MarkRegistry(
        screen_id="screen-1",
        marks={
            "ax_5": Mark(
                mark_id="ax_5",
                screen_id="screen-1",
                bbox=(0, 0, 500, 500),
                center=(250, 250),
                source="accessibility",
                role="View",
                text_summary="日历容器",
            ),
            "ax_9": Mark(
                mark_id="ax_9",
                screen_id="screen-1",
                bbox=(0, 100, 1000, 130),
                center=(500, 115),
                source="accessibility",
                role="TextView",
                text_summary="2026年10月",
            ),
            "ax_23": Mark(
                mark_id="ax_23",
                screen_id="screen-1",
                bbox=(0, 700, 1000, 730),
                center=(500, 715),
                source="accessibility",
                role="TextView",
                text_summary="2026年11月",
            ),
        },
        semantic_screen_id="semantic-1",
        observation_epoch=1,
        raw_screenshot_hash="hash-f",
    )


# ----------------------------------------------------------------------
# Adapter
# ----------------------------------------------------------------------


def test_adapter_parses_intent_locate_with_scope() -> None:
    action = adapt_json_action(
        {
            "type": "intent",
            "action": "locate",
            "target_text_hint": "10月2日",
            "scope_mark_id": "ax_5",
        }
    )

    assert action == {
        "_metadata": "intent",
        "action": "Locate",
        "target_text_hint": "10月2日",
        "scope_mark_id": "ax_5",
    }


def test_adapter_scope_without_hint_passes_through_grounding_rejects() -> None:
    # The adapter only validates field types; the missing-hint check for Locate
    # lives in grounding/validator where the registry is available.
    action = adapt_json_action(
        {"type": "intent", "action": "locate", "scope_mark_id": "ax_5"}
    )
    assert action == {
        "_metadata": "intent",
        "action": "Locate",
        "scope_mark_id": "ax_5",
    }
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            action,
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "missing_field"


def test_adapter_rejects_non_string_scope() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action(
            {
                "type": "intent",
                "action": "locate",
                "target_text_hint": "x",
                "scope_mark_id": 42,
            }
        )
    assert exc_info.value.code == "unsafe_value"


# ----------------------------------------------------------------------
# Validator
# ----------------------------------------------------------------------


def test_validator_accepts_locate_with_scope() -> None:
    action = validate_action(
        {
            "_metadata": "do",
            "action": "Locate",
            "target_text_hint": "10月2日",
            "scope_mark_id": "ax_5",
        }
    )
    assert action["scope_mark_id"] == "ax_5"


def test_validator_rejects_scope_with_unsafe_characters() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_mark_id": "bad id!",
            }
        )
    assert exc_info.value.code == "unsafe_value"


def test_validator_rejects_blank_scope() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_mark_id": "   ",
            }
        )
    assert exc_info.value.code == "missing_field"


def test_validator_rejects_non_string_scope() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_mark_id": ["ax_5"],
            }
        )
    assert exc_info.value.code == "unsafe_value"


# ----------------------------------------------------------------------
# P1: scope is mandatory — form A (scope_mark_id) or form B (interval)
# ----------------------------------------------------------------------


def test_validator_rejects_locate_without_scope() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "10月2日",
            }
        )
    assert exc_info.value.code == "missing_field"


def test_validator_rejects_both_scope_forms() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_mark_id": "ax_5",
                "scope_start_mark_id": "ax_9",
            }
        )
    assert exc_info.value.code == "unsafe_value"


def test_validator_rejects_end_without_start() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_end_mark_id": "ax_23",
            }
        )
    assert exc_info.value.code == "missing_field"


def test_validator_accepts_form_b_interval_with_end() -> None:
    action = validate_action(
        {
            "_metadata": "do",
            "action": "Locate",
            "target_text_hint": "10月1日",
            "scope_start_mark_id": "ax_9",
            "scope_end_mark_id": "ax_23",
        }
    )
    assert action["scope_start_mark_id"] == "ax_9"
    assert action["scope_end_mark_id"] == "ax_23"


def test_validator_accepts_form_b_interval_without_end() -> None:
    action = validate_action(
        {
            "_metadata": "do",
            "action": "Locate",
            "target_text_hint": "10月1日",
            "scope_start_mark_id": "ax_9",
        }
    )
    assert action["scope_start_mark_id"] == "ax_9"
    assert "scope_end_mark_id" not in action


def test_adapter_parses_form_b_interval_scope() -> None:
    action = adapt_json_action(
        {
            "type": "intent",
            "action": "locate",
            "target_text_hint": "10月1日",
            "scope_start_mark_id": "ax_9",
            "scope_end_mark_id": "ax_23",
        }
    )
    assert action == {
        "_metadata": "intent",
        "action": "Locate",
        "target_text_hint": "10月1日",
        "scope_start_mark_id": "ax_9",
        "scope_end_mark_id": "ax_23",
    }


def test_grounding_requires_scope() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月1日",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "missing_field"


def test_grounding_accepts_form_b_interval_when_marks_exist() -> None:
    action = ground_intent_to_action(
        {
            "_metadata": "intent",
            "action": "Locate",
            "target_text_hint": "10月1日",
            "scope_start_mark_id": "ax_9",
            "scope_end_mark_id": "ax_23",
        },
        mark_registry=_registry(),
        screen_id="screen-1",
    )
    assert action["scope_start_mark_id"] == "ax_9"
    assert action["scope_end_mark_id"] == "ax_23"


def test_grounding_accepts_form_b_interval_start_only() -> None:
    action = ground_intent_to_action(
        {
            "_metadata": "intent",
            "action": "Locate",
            "target_text_hint": "10月1日",
            "scope_start_mark_id": "ax_9",
        },
        mark_registry=_registry(),
        screen_id="screen-1",
    )
    assert action["scope_start_mark_id"] == "ax_9"
    assert "scope_end_mark_id" not in action


def test_grounding_rejects_form_b_start_missing_from_registry() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月1日",
                "scope_start_mark_id": "ax_99",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "scope_mark_unknown"


def test_grounding_rejects_form_b_end_missing_from_registry() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月1日",
                "scope_start_mark_id": "ax_9",
                "scope_end_mark_id": "ax_99",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "scope_mark_unknown"


def test_grounding_rejects_form_b_end_without_start() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月1日",
                "scope_end_mark_id": "ax_23",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "missing_field"


def test_grounding_rejects_form_b_invalidated_start() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月1日",
                "scope_start_mark_id": "ax_9",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
            invalidated_mark_ids=["ax_9"],
        )
    assert exc_info.value.code == "mark_invalidated"


# ----------------------------------------------------------------------
# Grounding: existence in the CURRENT registry is enforced here
# ----------------------------------------------------------------------


def test_grounding_passes_scope_through_when_mark_exists() -> None:
    action = ground_intent_to_action(
        {
            "_metadata": "intent",
            "action": "Locate",
            "target_text_hint": "10月2日",
            "scope_mark_id": "ax_5",
        },
        mark_registry=_registry(),
        screen_id="screen-1",
    )
    assert action == {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月2日",
        "scope_mark_id": "ax_5",
    }


def test_grounding_rejects_scope_not_in_registry() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月2日",
                "scope_mark_id": "ax_99",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "scope_mark_unknown"


def test_grounding_rejects_scope_without_registry() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "10月2日",
                "scope_mark_id": "ax_5",
            },
            mark_registry=None,
            screen_id="screen-1",
        )
    assert exc_info.value.code == "scope_mark_unknown"


def test_grounding_rejects_unsafe_scope_characters() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {
                "_metadata": "intent",
                "action": "Locate",
                "target_text_hint": "x",
                "scope_mark_id": "bad id!",
            },
            mark_registry=_registry(),
            screen_id="screen-1",
        )
    assert exc_info.value.code == "unsafe_value"
