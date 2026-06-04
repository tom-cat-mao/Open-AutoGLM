import pytest

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action, adapt_tool_calls
from phone_agent.actions.ir import ActionIR
from phone_agent.actions.repair import ActionRepairError, repair_action
from phone_agent.actions.safety import decide_safety
from phone_agent.actions.validator import ActionValidationError, validate_action


def test_adapt_json_maps_lowercase_tap_xy_to_canonical_action() -> None:
    action = adapt_json_action('{"type":"do","action":"tap","x":500,"y":250}')

    assert action == {"_metadata": "do", "action": "Tap", "element": [500, 250]}


def test_adapt_json_maps_mark_payload_to_intent_ir_not_canonical_action() -> None:
    action = adapt_json_action({"type": "intent", "action": "tap", "target_mark_id": "m1"})

    assert action == {"_metadata": "intent", "action": "Tap", "target_mark_id": "m1"}
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(action)
    assert exc_info.value.code == "invalid_metadata"


def test_adapt_json_maps_target_text_alias_to_intent_ir() -> None:
    action = adapt_json_action(
        {"type": "intent", "action": "tap", "target_text": "设置", "target_role": "button"}
    )

    assert action == {
        "_metadata": "intent",
        "action": "Tap",
        "target_text_hint": "设置",
        "target_role": "button",
    }


def test_adapt_json_rejects_provider_selection_in_intent() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action({"type": "intent", "action": "tap", "target_text_hint": "设置", "backend": "mlx"})

    assert exc_info.value.code == "unsafe_value"


def test_adapt_json_maps_finish_to_canonical_action() -> None:
    action = adapt_json_action({"type": "finish", "message": "done"})

    assert action == {"_metadata": "finish", "message": "done"}


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        ("not-json", "invalid_json"),
        ({"type": "do", "action": "unknown"}, "unknown_action"),
        ({"type": "do", "action": "tap", "x": 1}, "missing_field"),
        ({"type": "do", "action": "tap", "x": 1001, "y": 1}, "unsafe_value"),
        ({"type": "do", "action": "tap", "element": ["__import__", 1]}, "unsafe_value"),
    ),
)
def test_adapt_json_fails_closed_with_error_codes(payload, code: str) -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action(payload)

    assert exc_info.value.code == code


def test_adapt_tool_calls_accepts_single_whitelisted_call() -> None:
    action = adapt_tool_calls(
        [
            {
                "function": {
                    "name": "do",
                    "arguments": '{"type":"do","action":"tap","x":1,"y":2}',
                }
            }
        ]
    )

    assert action == {"_metadata": "do", "action": "Tap", "element": [1, 2]}


def test_adapt_tool_calls_infers_type_from_function_name() -> None:
    action = adapt_tool_calls(
        [
            {
                "function": {
                    "name": "finish",
                    "arguments": '{"message":"done"}',
                }
            }
        ]
    )

    assert action == {"_metadata": "finish", "message": "done"}


def test_adapt_json_swipe_uses_existing_tool_signature_fields() -> None:
    action = adapt_json_action(
        {"type": "do", "action": "swipe", "start": [100, 200], "end": [300, 400]}
    )

    assert action == {
        "_metadata": "do",
        "action": "Swipe",
        "start": [100, 200],
        "end": [300, 400],
    }


def test_adapt_json_wait_normalizes_numeric_duration() -> None:
    action = adapt_json_action({"type": "do", "action": "wait", "duration": 2})

    assert action == {"_metadata": "do", "action": "Wait", "duration": "2 seconds"}


def test_adapt_json_wait_requires_explicit_duration() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action({"type": "do", "action": "wait"})

    assert exc_info.value.code == "missing_field"


def test_adapt_tool_calls_rejects_unknown_tool() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_tool_calls([{"function": {"name": "shell", "arguments": "{}"}}])

    assert exc_info.value.code == "unsupported_tool_call"


def test_adapt_json_rejects_extra_dangerous_provider_fields() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_json_action({"type": "do", "action": "tap", "x": 1, "y": 2, "command": "rm -rf /"})

    assert exc_info.value.code == "unsafe_value"


def test_adapt_tool_calls_rejects_extra_dangerous_arguments() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_tool_calls(
            [
                {
                    "function": {
                        "name": "do",
                        "arguments": '{"type":"do","action":"tap","x":1,"y":2,"device_id":"x"}',
                    }
                }
            ]
        )

    assert exc_info.value.code == "unsafe_value"


def test_adapt_tool_calls_rejects_extra_dangerous_envelope_fields() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_tool_calls(
            [
                {
                    "function": {
                        "name": "do",
                        "arguments": '{"type":"do","action":"tap","x":1,"y":2}',
                        "device_id": "x",
                    }
                }
            ]
        )

    assert exc_info.value.code == "unsafe_value"


def test_adapt_tool_calls_rejects_invalid_envelope_type() -> None:
    with pytest.raises(ActionAdapterError) as exc_info:
        adapt_tool_calls(
            [
                {
                    "type": "not_function",
                    "function": {
                        "name": "do",
                        "arguments": '{"type":"do","action":"tap","x":1,"y":2}',
                    },
                }
            ]
        )

    assert exc_info.value.code == "unsupported_tool_call"


def test_validator_rejects_dangerous_extra_fields() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            {
                "_metadata": "do",
                "action": "Tap",
                "element": [500, 500],
                "command": "rm -rf /",
            }
        )

    assert exc_info.value.code == "unsafe_value"


def test_validator_rejects_unknown_action_and_bad_coordinates() -> None:
    with pytest.raises(ActionValidationError) as unknown_exc:
        validate_action({"_metadata": "do", "action": "Shell"})
    with pytest.raises(ActionValidationError) as coord_exc:
        validate_action({"_metadata": "do", "action": "Tap", "element": [1001, 1]})

    assert unknown_exc.value.code == "unknown_action"
    assert coord_exc.value.code == "unsafe_value"


def test_validator_rejects_unbounded_wait_duration() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action({"_metadata": "do", "action": "Wait", "duration": "999 seconds"})

    assert exc_info.value.code == "unsafe_value"


def test_validator_rejects_zero_wait_duration() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action({"_metadata": "do", "action": "Wait", "duration": "0 seconds"})

    assert exc_info.value.code == "unsafe_value"


def test_action_ir_metadata_is_authoritative_when_serializing() -> None:
    action = ActionIR(metadata="do", fields={"_metadata": "finish", "action": "Back"})

    assert action.to_dict() == {"_metadata": "do", "action": "Back"}


def test_repair_only_normalizes_safe_metadata_and_action_aliases() -> None:
    repaired = repair_action({"_metadata": "DO", "action": "tap", "element": [1, 2]})

    assert validate_action(repaired) == {"_metadata": "do", "action": "Tap", "element": [1, 2]}


def test_repair_does_not_invent_missing_coordinates() -> None:
    with pytest.raises(ActionRepairError) as exc_info:
        repair_action({"_metadata": "do", "action": "Tap"}, error_code="missing_field")

    assert exc_info.value.code == "repair_not_applicable"


def test_safety_gate_routes_sensitive_actions_without_execution() -> None:
    confirm = decide_safety(
        {"_metadata": "do", "action": "Tap", "element": [500, 500], "message": "支付确认"}
    )
    takeover = decide_safety({"_metadata": "do", "action": "Take_over", "message": "验证码"})
    approved = decide_safety({"_metadata": "do", "action": "Back"})

    assert confirm.route == "confirm"
    assert confirm.interrupt_type == "confirmation"
    assert takeover.route == "takeover"
    assert takeover.interrupt_type == "takeover"
    assert approved.route == "approved"


def test_mark_grounding_preserves_sensitive_context_for_safety_gate() -> None:
    from phone_agent.actions.grounding import ground_intent_to_action
    from phone_agent.graph.marks import MarkRegistry

    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "m1",
                "screen_id": "screen-1",
                "bbox": [100, 100, 200, 200],
                "role": "button",
                "text_summary": "支付确认",
            }
        ],
    )

    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "target_mark_id": "m1"},
        mark_registry=registry,
        screen_id="screen-1",
    )

    decision = decide_safety(action)

    assert action["message"] == "Sensitive mark-grounded tap requires confirmation"
    assert decision.route == "confirm"


def test_mark_grounding_routes_login_otp_context_to_takeover() -> None:
    from phone_agent.actions.grounding import ground_intent_to_action
    from phone_agent.graph.marks import MarkRegistry

    registry = MarkRegistry.from_marks(
        "screen-1",
        [{"mark_id": "m1", "screen_id": "screen-1", "bbox": [100, 100, 200, 200], "text_summary": "验证码"}],
    )

    action = ground_intent_to_action(
        {"_metadata": "intent", "action": "tap", "target_mark_id": "m1"},
        mark_registry=registry,
        screen_id="screen-1",
    )

    decision = decide_safety(action)

    assert action == {
        "_metadata": "do",
        "action": "Take_over",
        "message": "Sensitive mark-grounded action requires takeover",
    }
    assert decision.route == "takeover"


def test_mark_registry_rejects_prompt_injection_metadata() -> None:
    from phone_agent.graph.marks import MarkRegistry

    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "m1\nignore_previous",
                "bbox": [100, 100, 200, 200],
                "source": "ocr\nraw text",
                "role": "button\nignore",
            }
        ],
    )

    assert registry.marks == {}


def test_validate_action_normalizes_launch_app_alias() -> None:
    from phone_agent.actions.validator import validate_action

    action = {"_metadata": "do", "action": "Launch", "app": "设置"}
    result = validate_action(action)
    assert result["app"] == "Settings"


def test_validate_action_normalizes_launch_app_case() -> None:
    from phone_agent.actions.validator import validate_action

    action = {"_metadata": "do", "action": "Launch", "app": "chrome"}
    result = validate_action(action)
    assert result["app"] == "Chrome"


def test_validate_action_rejects_unknown_launch_app() -> None:
    from phone_agent.actions.validator import ActionValidationError, validate_action

    action = {"_metadata": "do", "action": "Launch", "app": "SomeNewApp"}
    with pytest.raises(ActionValidationError, match="unknown app"):
        validate_action(action)


def test_build_screen_belief_accepts_derived_fields() -> None:
    from phone_agent.graph.context import build_screen_belief

    belief = build_screen_belief(
        current_app="Settings",
        step_count=5,
        summary="WiFi page is open",
        loading_or_blocked=True,
        unsafe_or_sensitive=False,
        confidence="high",
    )

    assert belief["loading_or_blocked"] is True
    assert belief["unsafe_or_sensitive"] is False
    assert belief["confidence"] == "high"
    assert belief["current_app"] == "Settings"
    assert belief["summary"]["redacted"] is True


def test_build_screen_belief_redacts_free_text_summary_by_default() -> None:
    from phone_agent.graph.context import build_plan_context_block, build_screen_belief

    belief = build_screen_belief(
        current_app="Chat",
        step_count=2,
        summary="Please contact Alice about internal pricing",
    )
    block, _ = build_plan_context_block({"screen_belief": belief}, lang="en")

    assert "Please contact Alice" not in block
    assert belief["summary"]["redacted"] is True


def test_build_screen_belief_defaults() -> None:
    from phone_agent.graph.context import build_screen_belief

    belief = build_screen_belief(current_app="Chrome", step_count=1)

    assert belief["loading_or_blocked"] is False
    assert belief["unsafe_or_sensitive"] is False
    assert belief["confidence"] == "medium"
