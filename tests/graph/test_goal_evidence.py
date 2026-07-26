from phone_agent.graph.goal_evidence import (
    append_evaluation_entries,
    target_app_entered,
    unattested_raw_text_bindings,
)
from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import PureGoalEvaluator
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


def test_evidence_ledger_is_bounded_and_excludes_runtime_values() -> None:
    evaluation = {
        "evidence": {
            "per_criterion": {
                "topic": {
                    "status": "contradicted",
                    "reason": "target_mismatch",
                    "source": "visual_region",
                    "confidence_bucket": "high",
                    "expected_value": "Silverstone",
                    "observed_value": "Singapore",
                }
            }
        }
    }

    ledger = append_evaluation_entries(
        [],
        evaluation=evaluation,
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=2,
        predicate_ids={"topic": "semantic.entity_matches"},
        limit=1,
    )

    assert ledger == [
        {
            "criterion_id": "topic",
            "predicate_id": "semantic.entity_matches",
            "status": "contradicted",
            "reason_code": "target_mismatch",
            "source_kind": "visual_region",
            "confidence_bucket": "high",
            "contract_id": "contract-1",
            "screen_id": "screen-1",
            "observation_epoch": 2,
        }
    ]
    assert "Silverstone" not in str(ledger)
    assert "Singapore" not in str(ledger)


def test_absent_raw_text_binding_degrades_without_becoming_a_veto() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="locate target",
        objective_length=13,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "role-name"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = []
    for epoch in range(1, 4):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        "topic": {
                            "status": "unknown",
                            "reason": "not_observed_in_view",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id="contract-1",
            screen_id=f"screen-{epoch}",
            observation_epoch=epoch,
            predicate_ids={"topic": "semantic.entity_matches"},
            target_app_entered=True,
        )

    assert unattested_raw_text_bindings(
        ledger,
        contract,
        contract_id="contract-1",
    ) == ["topic"]
    evaluation = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id="contract-1",
        evidence_ledger=ledger,
        finish_claim_matched=["topic"],
        screen_id="screen-3",
        observation_epoch=3,
    )
    assert evaluation.status == "unknown"
    assert not evaluation.missing


def test_observed_raw_text_binding_never_later_degrades() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="locate target",
        objective_length=13,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "target"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = []
    for epoch, status in enumerate(("matched", *("unknown" for _ in range(64))), start=1):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        "topic": {
                            "status": status,
                            "reason": "accessibility_observation",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id="contract-1",
            screen_id=f"screen-{epoch}",
            observation_epoch=epoch,
            predicate_ids={"topic": "semantic.entity_matches"},
            target_app_entered=True,
        )

    assert unattested_raw_text_bindings(
        ledger, contract, contract_id="contract-1"
    ) == []


def test_raw_text_attestation_waits_for_target_app_and_accessibility() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="locate target",
        objective_length=13,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "target"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = []
    for epoch in range(1, 4):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        "topic": {
                            "status": "unknown",
                            "reason": "not_observed_in_view",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id="contract-1",
            screen_id=f"screen-{epoch}",
            observation_epoch=epoch,
            predicate_ids={"topic": "semantic.entity_matches"},
            target_app_entered=False,
        )

    assert unattested_raw_text_bindings(
        ledger, contract, contract_id="contract-1"
    ) == []

    for epoch in range(4, 6):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        "topic": {
                            "status": "unknown",
                            "reason": "not_observed_in_view",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id="contract-1",
            screen_id=f"screen-{epoch}",
            observation_epoch=epoch,
            predicate_ids={"topic": "semantic.entity_matches"},
            target_app_entered=True,
        )

    assert unattested_raw_text_bindings(
        ledger, contract, contract_id="contract-1"
    ) == []
    ledger = append_evaluation_entries(
        ledger,
        evaluation={
            "evidence": {
                "per_criterion": {
                    "topic": {
                        "status": "unknown",
                        "reason": "not_observed_in_view",
                        "source": "accessibility",
                    }
                }
            }
        },
        contract_id="contract-1",
        screen_id="screen-6",
        observation_epoch=6,
        predicate_ids={"topic": "semantic.entity_matches"},
        target_app_entered=True,
    )
    assert unattested_raw_text_bindings(
        ledger, contract, contract_id="contract-1"
    ) == ["topic"]


def test_target_app_entry_does_not_require_a_foreground_criterion() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="observe settings label",
        objective_length=22,
        success_criteria=[
            SuccessCriterion(
                "label",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "target"
                ),
            )
        ],
        target_app_hint="settings",
        compile_status="compiled",
    )

    assert target_app_entered(
        contract,
        collected=None,
        current_app="com.android.settings",
        foreground_activity="com.android.settings/.Settings",
    )


def test_private_typed_expected_value_is_not_serialized_to_agent_state() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="open target content",
        objective_length=19,
        success_criteria=[
            SuccessCriterion(
                name="topic",
                description="topic matches",
                verification="vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "Silverstone"
                ),
            )
        ],
        compile_status="compiled",
    )

    serialized = contract.to_dict()

    assert "Silverstone" not in str(serialized)
    predicate = serialized["success_criteria"][0]["predicate"]
    assert predicate["predicate_id"] == "semantic.entity_matches"
    assert predicate["expected_value"] is None
    assert predicate["expected_value_projection"] == "metadata"


def test_goal_contract_redacts_all_state_and_prompt_text_fields() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="contact private@example.com at 13800138000",
        objective_length=42,
        success_criteria=[
            SuccessCriterion(
                name="done",
                description="order ORD-123 for private@example.com is visible",
                verification="vlm_judge",
            )
        ],
        constraints=["do not expose 13800138000"],
        non_goals=["email private@example.com"],
        compile_status="compiled",
    )

    serialized = str(contract.to_dict())
    prompt = contract.to_prompt_block(lang="en")

    for sensitive in ("private@example.com", "13800138000"):
        assert sensitive not in serialized
        assert sensitive not in prompt


def test_public_typed_predicate_round_trips_through_contract_state() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="open app",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(
                name="app",
                description="app foreground",
                verification="app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        compile_status="compiled",
    )

    restored = GoalContract.from_dict(contract.to_dict())

    assert restored.success_criteria[0].predicate is not None
    assert restored.success_criteria[0].predicate.expected_value == "settings"


def test_pure_goal_evaluator_folds_only_current_bound_ledger() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="open app",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(
                name="app",
                description="app foreground",
                verification="app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = [
        {
            "criterion_id": "app",
            "predicate_id": "app.foreground_identity",
            "status": "matched",
            "reason_code": "values_match",
            "source_kind": "device",
            "confidence_bucket": "high",
            "contract_id": "contract-1",
            "screen_id": "screen-1",
            "observation_epoch": 2,
        }
    ]

    result = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=["app"],
        screen_id="screen-1",
        observation_epoch=2,
    )
    stale = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=["app"],
        screen_id="screen-2",
        observation_epoch=3,
    )

    assert result.status == "success"
    assert stale.status == "failure"
    assert stale.evidence["per_criterion"]["app"]["status"] == "stale"


def test_pure_goal_evaluator_rejects_unknown_finish_claim_ids() -> None:
    contract = GoalContract(
        task_hash="contract-1",
        redacted_objective="open app",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(
                name="app",
                description="app foreground",
                verification="app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = [
        {
            "criterion_id": "app",
            "predicate_id": "app.foreground_identity",
            "status": "matched",
            "reason_code": "values_match",
            "source_kind": "device",
            "confidence_bucket": "high",
            "contract_id": "contract-1",
            "screen_id": "screen-1",
            "observation_epoch": 2,
        }
    ]

    result = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=["app", "invented_criterion"],
        screen_id="screen-1",
        observation_epoch=2,
    )

    assert result.status == "failure"
    assert result.evidence["unknown_finish_claim_ids"] == ["invented_criterion"]
