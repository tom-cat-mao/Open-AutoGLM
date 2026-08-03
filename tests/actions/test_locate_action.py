"""F1 locate tool: adapter/validator/safety/capability/grounding registration."""

import pytest

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.capability import get_tool_capability
from phone_agent.actions.grounding import GroundingError, ground_intent_to_action
from phone_agent.actions.safety import decide_safety
from phone_agent.actions.validator import ActionValidationError, validate_action
from phone_agent.config.policy import LOCATE_MAX_PER_RUN


# ----------------------------------------------------------------------
# Adapter
# ----------------------------------------------------------------------


def test_adapter_parses_intent_locate() -> None:
    action = adapt_json_action(
        {"type": "intent", "action": "locate", "target_text_hint": "10月1日"}
    )

    assert action == {
        "_metadata": "intent",
        "action": "Locate",
        "target_text_hint": "10月1日",
    }


def test_adapter_parses_do_locate() -> None:
    # A payload carrying target_text_hint is interpreted as an IntentIR (the
    # canonical locate form) regardless of a `do` type tag, matching tap-like
    # intent fields; grounding compiles it to the executable do action later.
    action = adapt_json_action(
        {"type": "do", "action": "locate", "target_text_hint": "搜索按钮"}
    )

    assert action == {
        "_metadata": "intent",
        "action": "Locate",
        "target_text_hint": "搜索按钮",
    }


def test_adapter_do_locate_without_intent_field_fails_closed() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action({"type": "do", "action": "locate", "message": "x"})
    assert exc_info.value.code == "missing_field"


def test_adapter_do_locate_requires_hint() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action({"type": "do", "action": "locate"})
    assert exc_info.value.code == "missing_field"


def test_adapter_rejects_extra_fields_on_locate() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action(
            {
                "type": "do",
                "action": "locate",
                "target_text_hint": "x",
                "provider": "backend",
            }
        )
    assert exc_info.value.code == "unsafe_value"


# ----------------------------------------------------------------------
# Validator
# ----------------------------------------------------------------------


def test_validator_accepts_locate_with_hint() -> None:
    action = validate_action(
        {"_metadata": "do", "action": "Locate", "target_text_hint": "10月1日"}
    )
    assert action["action"] == "Locate"
    assert action["target_text_hint"] == "10月1日"


def test_validator_rejects_locate_without_hint() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action({"_metadata": "do", "action": "Locate"})
    assert exc_info.value.code == "missing_field"


def test_validator_rejects_blank_hint() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action({"_metadata": "do", "action": "Locate", "target_text_hint": "   "})
    assert exc_info.value.code == "missing_field"


def test_validator_rejects_oversized_hint() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x" * 241,
            }
        )
    assert exc_info.value.code == "unsafe_value"


def test_validator_rejects_extra_fields_on_locate() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Locate",
                "target_text_hint": "x",
                "element": [1, 2],
            }
        )
    assert exc_info.value.code == "unsafe_value"


# ----------------------------------------------------------------------
# Safety gate: benign, never confirm/takeover
# ----------------------------------------------------------------------


def test_safety_gate_approves_locate_without_hitl() -> None:
    decision = decide_safety(
        {"_metadata": "do", "action": "Locate", "target_text_hint": "x"}
    )
    assert decision.route == "approved"
    assert decision.interrupt_type is None


# ----------------------------------------------------------------------
# Capability: internal, no reobservation, cannot advance goal
# ----------------------------------------------------------------------


def test_locate_capability_is_internal_non_progress() -> None:
    capability = get_tool_capability("Locate")
    assert capability is not None
    assert capability.implementation_status == "implemented"
    assert capability.side_effect_kind == "none"
    assert capability.observation_effect == "none"
    assert capability.can_advance_goal is False
    assert capability.requires_reobservation is False


def test_locate_budget_constant_is_positive() -> None:
    assert LOCATE_MAX_PER_RUN >= 1


# ----------------------------------------------------------------------
# Grounding: Locate passes through as a canonical do action
# ----------------------------------------------------------------------


def test_grounding_passes_locate_intent_through_untouched() -> None:
    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "Locate", "target_text_hint": "10月1日"},
        mark_registry=None,
        screen_id="screen-1",
    )
    assert action == {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月1日",
    }


def test_grounding_locate_requires_hint() -> None:
    with pytest.raises(GroundingError) as exc_info:
        ground_intent_to_action(
            {"_metadata": "intent", "action": "Locate"},
            mark_registry=None,
            screen_id="screen-1",
        )
    assert exc_info.value.code == "missing_field"


def test_grounding_does_not_resolve_locate_to_a_mark() -> None:
    """Locate must never be grounded into a tap: it stays an internal action."""
    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "Locate", "target_text_hint": "搜索按钮"},
        mark_registry={"screen_id": "screen-1", "marks": {}},
        screen_id="screen-1",
    )
    assert action["action"] == "Locate"
    assert "_metadata" in action and action["_metadata"] == "do"
