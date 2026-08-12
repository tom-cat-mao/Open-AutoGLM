from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action
from phone_agent.actions.validator import (
    ActionValidationError,
    _whitelist_found,
    validate_action,
)
from phone_agent.graph.edges import after_plan
from phone_agent.graph.goal_evaluator import GoalEvaluation
from phone_agent.graph.guidance import (
    mechanism_suggestion_for,
    retry_policy_for_layer,
    screenshot_error_fields,
)
from phone_agent.graph.nodes.acceptance import _rejected
from phone_agent.graph.nodes.plan import (
    _build_parse_retry_messages,
    _retry_policy_for_layer,
    _screenshot_error_fields,
    plan_node,
)
from phone_agent.graph.state import AgentState


@dataclass
class FakeModelResponse:
    thinking: str
    action: str
    parse_metadata: dict | None = None


class FakeModelClient:
    def __init__(self, responses: list[FakeModelResponse]) -> None:
        self.responses = responses
        self.calls = 0
        self.messages: list[dict] | None = None

    def request(self, messages, **kwargs):
        self.messages = messages
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def _plan_config(model: FakeModelClient, fake_device, *, parse_retry: int = 0) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "output_mode": "json_schema",
            "grounding_provider_name": "off",
            "parse_retry": parse_retry,
            "verbose": False,
        }
    }


def test_j1_validator_expected_found_and_private_whitelist() -> None:
    with pytest.raises(ActionValidationError) as missing:
        validate_action({"_metadata": "do", "action": "Type"})
    assert missing.value.expected == {"field": "text", "type": "string"}
    assert missing.value.found == {
        "field": "text",
        "value": None,
    }

    with pytest.raises(ActionValidationError) as unknown:
        validate_action({"_metadata": "do", "action": "Explode"})
    assert unknown.value.found == {"action": "Explode"}

    with pytest.raises(ActionValidationError) as coord:
        validate_action({"_metadata": "do", "action": "Tap", "element": [500, 1234]})
    assert coord.value.expected == {"field": "element[1]", "range": "0..1000"}
    assert coord.value.found == {"field": "element[1]", "value": 1234}

    assert _whitelist_found({"field": "message", "value": "secret text"}) == {
        "field": "message",
        "value": {"redacted": True, "length": 11},
    }


def test_j1_adapter_expected_found_for_mechanical_errors() -> None:
    with pytest.raises(ActionAdapterError) as missing:
        adapt_json_action({"type": "do", "action": "wait"})
    assert missing.value.expected == {"field": "duration", "type": "string|number"}
    assert missing.value.found == {"field": "duration", "value": None}

    with pytest.raises(ActionAdapterError) as unknown:
        adapt_json_action({"type": "do", "action": "unknown"})
    assert unknown.value.found == {"action": "unknown"}

    with pytest.raises(ActionAdapterError) as mark_required:
        adapt_json_action({"type": "do", "action": "tap"})
    assert mark_required.value.expected == {
        "field": "target_mark_id",
        "type": "mark_id_or_object_selector",
    }


def test_j1_guidance_mapping_preserves_plan_error_policy_helpers() -> None:
    for layer in ("parse", "adapter", "validation", "grounding"):
        assert _retry_policy_for_layer(layer) == retry_policy_for_layer(layer)
    for code, sensitive in (
        ("secure_screenshot_blocked", False),
        ("invalid_screenshot", False),
        ("invalid_screenshot", True),
    ):
        assert _screenshot_error_fields(code, sensitive=sensitive) == screenshot_error_fields(
            code, sensitive=sensitive
        )
    assert (
        mechanism_suggestion_for("missing_field", "validation")
        == "Re-emit the action with all required fields populated."
    )
    assert len(mechanism_suggestion_for("unknown_mark", "grounding") or "") <= 120


def test_j1_parse_retry_message_includes_validator_message() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "now"}]}]
    retry = _build_parse_retry_messages(
        messages, "Model parse failed: validation: missing_field: text must be a string"
    )
    text = retry[-1]["content"][0]["text"]
    assert "Validator message:" in text
    assert "missing_field" in text


def test_j1_plan_adapter_failure_writes_contract_and_replans_once(
    base_state, fake_device
) -> None:
    model = FakeModelClient(
        [
            FakeModelResponse("", '{"type":"do","action":"wait"}'),
            FakeModelResponse("", '{"type":"do","action":"wait","duration":"1 seconds"}'),
        ]
    )
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", after_plan, {"replan": "plan", "execute": END}
    )
    app = graph.compile()

    result = app.invoke(base_state, _plan_config(model, fake_device, parse_retry=0))

    assert model.calls == 2
    assert result["validation_replan_count"] == 1
    assert result["parse_failure"] is None
    assert result["mechanism_suggestion"] is None
    assert result["action_parsed"] == {
        "_metadata": "do",
        "action": "Wait",
        "duration": "1 seconds",
    }


def test_j1_plan_adapter_failure_second_time_stays_terminal(
    base_state, fake_device
) -> None:
    base_state["validation_replan_count"] = 1
    model = FakeModelClient([FakeModelResponse("", '{"type":"do","action":"wait"}')])

    result = plan_node(base_state, _plan_config(model, fake_device, parse_retry=0))

    assert result["finished"] is True
    assert result["parse_failure"] == {
        "code": "missing_field",
        "layer": "adapter",
        "expected": {"field": "duration", "type": "string|number"},
        "found": {"field": "duration", "value": None},
    }
    assert result["mechanism_suggestion"]
    assert result["action_result"]["error_layer"] == "adapter"
    assert result["action_result"]["retry_policy"] == "parse_retry"


def test_j1_recovery_branch_message_uses_code_only(base_state, fake_device) -> None:
    base_state["failure_cause"] = "wrong_page"
    base_state["suggested_strategy"] = "go_back"
    model = FakeModelClient([FakeModelResponse("", "not json")])

    result = plan_node(base_state, _plan_config(model, fake_device, parse_retry=0))

    assert result["action_parsed"] == {"_metadata": "do", "action": "Back"}
    assert result["action_result"]["message"] == "recovery_from:invalid_json"
    assert "not json" not in result["action_result"]["message"]
    assert result["parse_failure"]["code"] == "invalid_json"


def test_j1_acceptance_verdict_projection_status_reason_only(base_state) -> None:
    evaluation = GoalEvaluation(
        status="unknown",
        missing=["target"],
        evidence={
            "per_criterion": {
                "target": {
                    "status": "unknown",
                    "reason": "criterion_unobserved",
                    "observed_value": "raw private text",
                    "screen_reference": "raw screen",
                },
                "blocked": {
                    "status": "contradicted",
                    "reason": "programmatic_contradiction",
                    "observed_value": "raw private text",
                },
                "done": {"status": "satisfied", "reason": "model_observed"},
            }
        },
    )

    result = _rejected(
        base_state,
        context_mode="inject",
        evaluation=evaluation,
        message="no",
    )

    assert result["acceptance_verdicts"] == {
        "target": {"status": "unknown", "reason": "criterion_unobserved"},
        "blocked": {"status": "contradicted", "reason": "programmatic_contradiction"},
    }
    assert "observed_value" not in str(result["acceptance_verdicts"])
    assert "screen_reference" not in str(result["acceptance_verdicts"])


def test_j1_state_declares_guidance_fields() -> None:
    annotations: dict[str, Any] = AgentState.__annotations__
    for key in (
        "parse_failure",
        "mechanism_suggestion",
        "acceptance_verdicts",
        "validation_replan_count",
    ):
        assert key in annotations
