"""J-batch integration: producer-written guidance fields must render in plan context.

J1 (producers: plan/acceptance) and J2 (renderer: context.py) were built in
isolation against docs/execution-j-guidance-contract.md. These tests wire the
real producer output into the real renderer — no fake shapes on either side.
"""

from __future__ import annotations

from typing import Any

from phone_agent.graph.context import build_plan_context_block
from phone_agent.graph.goal_evaluator import GoalEvaluation
from phone_agent.graph.nodes.acceptance import _project_acceptance_verdicts
from phone_agent.graph.nodes.plan import plan_node


class _BadFieldModel:
    """Emits a Tap action missing the required target — adapter missing_field."""

    def request(self, messages, **kwargs):
        class _Response:
            thinking = "t"
            action = '{"_metadata": "do", "action": "Tap"}'
            parse_metadata = None

        return _Response()


def _plan_config(model: Any, fake_device) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "output_mode": "json_schema",
            "grounding_provider_name": "off",
            "parse_retry": 0,
            "verbose": False,
        }
    }


def test_j_integration_plan_failure_fields_render_in_context(base_state, fake_device):
    """plan validation failure -> replan-once state -> context block renders guidance."""
    model = _BadFieldModel()
    update = plan_node(base_state, _plan_config(model, fake_device))

    # Producer side (C7): first failure replans instead of terminating.
    assert update["finished"] is False
    assert update["validation_replan_count"] == 1
    assert update["parse_failure"]["layer"] in {"adapter", "validation"}
    assert update["mechanism_suggestion"]

    # Renderer side: feed the real producer update into the real context builder.
    rendered_state = {**base_state, **update}
    block, _ = build_plan_context_block(rendered_state, "tap the button", consumer="inject")
    assert "parse_failure:" in block
    assert "code=mark_required" in block
    assert "[system_guidance]" in block


def test_j_integration_acceptance_verdicts_project_and_render(base_state):
    """Real _project_acceptance_verdicts output must render in acceptance_rejection."""
    evaluation = GoalEvaluation(
        status="failure",
        missing=["cheapest_sort"],
        evidence={
            "per_criterion": {
                "cheapest_sort": {
                    "status": "unknown",
                    "reason": "no cited evidence",
                    "observed_value": "RAW-SCREEN-TEXT-MUST-NOT-RENDER",
                },
                "time_window": {"status": "satisfied", "reason": "sealed"},
            }
        },
    )
    verdicts = _project_acceptance_verdicts(evaluation.to_dict(), task_context=None)

    # Producer contract: only status + reason, and only unsettled criteria.
    assert set(verdicts) == {"cheapest_sort"}
    assert verdicts["cheapest_sort"]["status"] == "unknown"
    assert "observed_value" not in str(verdicts)

    rendered_state = {
        **base_state,
        "acceptance_verdicts": verdicts,
        "finish_validation_status": "failed",
        "reflection": "sort control never observed applied",
        "acceptance_rejection_feedback": {
            "missing": [
                {"criterion": "cheapest_sort", "stage_id": "terminal", "hint": "re-observe"}
            ]
        },
        "goal_contract": {"criteria": [{"name": "cheapest_sort", "verification": "vlm_judge"}]},
    }
    block, _ = build_plan_context_block(rendered_state, "task", consumer="inject")
    assert "acceptance_rejection" in block
    assert "verdict:" in block and "cheapest_sort" in block
    assert "judge:" in block
    assert "RAW-SCREEN-TEXT-MUST-NOT-RENDER" not in block


def test_j_integration_success_path_clears_guidance(base_state, fake_device):
    """A successful plan step must clear parse_failure/mechanism_suggestion."""
    base_state["parse_failure"] = {"layer": "validation", "code": "missing_field"}
    base_state["mechanism_suggestion"] = "stale"
    base_state["validation_replan_count"] = 1

    class _GoodModel:
        def request(self, messages, **kwargs):
            class _Response:
                thinking = "t"
                action = '{"_metadata": "do", "action": "Wait", "duration": "1 seconds"}'
                parse_metadata = None

            return _Response()

    update = plan_node(base_state, _plan_config(_GoodModel(), fake_device))
    assert update.get("parse_failure") is None
    assert update.get("mechanism_suggestion") is None
