"""Strict characterization tests for gaps scheduled after Phase 0."""

from types import SimpleNamespace

import pytest

from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import evaluate_finish_claim
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG
from phone_agent.graph.tools.misc import call_api, interact, note


def test_unknown_foreground_package_is_not_reported_as_system_home(monkeypatch) -> None:
    from phone_agent.adb import device

    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="mCurrentFocus=Window{42 u0 com.example.unknown/.MainActivity}",
            returncode=0,
        ),
    )

    assert device.get_current_app() != "System Home"


@pytest.mark.parametrize("stub_tool", [note, call_api, interact])
def test_stub_capability_does_not_report_execution_success(stub_tool) -> None:
    result = stub_tool.invoke({"message": "characterization"})

    assert result["success"] is False


def test_wrong_content_topic_cannot_satisfy_named_vlm_criterion() -> None:
    contract = GoalContract(
        task_hash="fixture",
        redacted_objective="Open Monica content about Silverstone",
        objective_length=38,
        success_criteria=[
            SuccessCriterion(
                name="content_topic",
                description="Expected topic: Silverstone",
                verification="vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "Silverstone"
                ),
            )
        ],
        compile_status="compiled",
        compile_source="external",
    )

    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["content_topic"],
        reflect_named_evidence=[
            {
                "criterion": "content_topic",
                "screen_reference": "current_screen:media_region",
                "observed_value": "Singapore",
            }
        ],
    )

    assert result.status != "success"
