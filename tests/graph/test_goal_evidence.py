from phone_agent.graph.goal_evidence import (
    append_evaluation_entries,
    append_model_observations,
    fresh_observation_count,
    latest_status_by_criterion,
    target_app_entered,
    unattested_raw_text_bindings,
)
from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import PureGoalEvaluator, fold_acceptance_verdicts
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


def _obs(criterion: str, status: str, step: int) -> dict:
    return {
        "kind": "model_observation",
        "contract_id": "c1",
        "criterion": criterion,
        "status": status,
        "observed_value": None,
        "step": step,
    }


def test_fresh_observation_same_status_repeat_is_not_fresh() -> None:
    """F6: repeating the same status for a criterion is NOT fresh — the old
    raw-count signal was nearly always > 0 with a goal contract (fail-open)."""
    ledger = [_obs("a", "observed", 1)]
    assert (
        fresh_observation_count(
            [{"criterion": "a", "status": "observed"}],
            ledger,
            contract_id="c1",
        )
        == 0
    )


def test_fresh_observation_status_flip_is_fresh() -> None:
    ledger = [_obs("a", "not_visible", 1), _obs("b", "observed", 1)]
    assert (
        fresh_observation_count(
            [
                {"criterion": "a", "status": "observed"},
                {"criterion": "b", "status": "observed"},
            ],
            ledger,
            contract_id="c1",
        )
        == 1  # only "a" flipped; "b" repeated its status
    )


def test_fresh_observation_first_ever_is_fresh() -> None:
    """首见即新鲜: no prior record for the criterion → any observation counts."""
    assert (
        fresh_observation_count(
            [{"criterion": "a", "status": "not_visible"}],
            [],
            contract_id="c1",
        )
        == 1
    )


def test_latest_status_by_criterion_last_record_wins() -> None:
    ledger = [
        _obs("a", "not_visible", 1),
        _obs("a", "observed", 2),
        _obs("b", "observed", 2),
        {"kind": "effect_event", "contract_id": "c1", "criterion_id": "a"},
    ]
    statuses = latest_status_by_criterion(ledger, contract_id="c1")
    assert statuses == {"a": "observed", "b": "observed"}


def test_fresh_observation_ignores_other_contracts() -> None:
    ledger = [_obs("a", "observed", 1)]
    ledger[0]["contract_id"] = "other"
    assert (
        fresh_observation_count(
            [{"criterion": "a", "status": "observed"}],
            ledger,
            contract_id="c1",
        )
        == 1  # no prior record under c1 -> first-ever fresh
    )


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


def test_pure_goal_evaluator_records_unknown_finish_claim_ids_diagnostically() -> None:
    """A finish claim naming outside the contract is diagnostic only: recorded
    in evidence but never changes the verdict. With the required criterion
    matched on the ledger, an extra invented claim id cannot force failure
    (the declarative evaluator whitelists names before they can satisfy)."""
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

    # Ledger says app matched → success despite the stray claim name.
    assert result.status == "success"
    assert result.matched == ["app"]
    # Still recorded for trace diagnosis.
    assert result.evidence["unknown_finish_claim_ids"] == ["invented_criterion"]


# ----------------------------------------------------------------------
# Fix B: per-criterion anchors survive FIFO observation eviction
# ----------------------------------------------------------------------


def _evict_observation(ledger: list[dict], contract_id: str = "c1") -> list[dict]:
    """Fill the FIFO observation window with unrelated criteria so the
    earliest per-criterion records crop out (MODEL_OBSERVATION_LIMIT=48)."""

    for step in range(2, 10):
        obs = [
            {"criterion": f"x{i}", "status": "observed"} for i in range(6)
        ]
        ledger = append_model_observations(
            ledger,
            contract_id=contract_id,
            observations=obs,
            step=step,
            screen_id=f"s{step}",
            observation_epoch=step,
        )
    return ledger


def _single_judge_contract() -> GoalContract:
    return GoalContract(
        task_hash="c1",
        redacted_objective="target visible",
        objective_length=15,
        success_criteria=[
            SuccessCriterion("target", "target visible", "vlm_judge")
        ],
        compile_status="compiled",
    )


def test_observation_anchor_upserts_one_per_criterion_and_never_evicts() -> None:
    """Fix B: one anchor per (contract, criterion), same redacted shape as the
    observation, NOT subject to the FIFO window (schema add-only)."""
    ledger = append_model_observations(
        [],
        contract_id="c1",
        observations=[
            {"criterion": "c", "status": "observed", "observed_value": "v1"}
        ],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = append_model_observations(
        ledger,
        contract_id="c1",
        observations=[
            {"criterion": "c", "status": "observed", "observed_value": "v2"}
        ],
        step=2,
        screen_id="s2",
        observation_epoch=2,
    )
    anchors = [e for e in ledger if e.get("kind") == "observation_anchor"]
    assert len(anchors) == 1
    assert anchors[0]["criterion"] == "c"
    assert anchors[0]["observed_value"] == "v2"
    assert "kind" in anchors[0] and anchors[0]["kind"] == "observation_anchor"

    # 49 windowed observations crop BOTH early c records; the anchor stays.
    # Each criterion keeps exactly one anchor (upper bound = criterion count).
    ledger = _evict_observation(ledger)
    anchors = [e for e in ledger if e.get("kind") == "observation_anchor"]
    assert len(anchors) == 7  # c + the six filler criteria
    c_anchors = [e for e in anchors if e.get("criterion") == "c"]
    assert len(c_anchors) == 1
    assert c_anchors[0]["status"] == "observed"
    windowed = [e for e in ledger if e.get("kind") == "model_observation"]
    assert len(windowed) <= 48
    assert all(e.get("step") != 1 for e in windowed)


def test_fresh_observation_same_status_not_fresh_after_eviction() -> None:
    """Fix B: eviction used to erase a criterion's only record, making the
    next same-status read look first-ever (fresh=1 → the dead-loop guard
    reset); the anchor keeps the previous status so the repeat stays
    not-fresh."""
    ledger = append_model_observations(
        [],
        contract_id="c1",
        observations=[{"criterion": "c0", "status": "observed"}],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = _evict_observation(ledger)  # c0's step-1 record is evicted
    assert fresh_observation_count(
        [{"criterion": "c0", "status": "observed"}],
        ledger,
        contract_id="c1",
    ) == 0


def test_fresh_observation_status_flip_after_eviction_is_fresh() -> None:
    """Fix B: an anchor never masks a real status flip — a different status
    after eviction still counts as fresh."""
    ledger = append_model_observations(
        [],
        contract_id="c1",
        observations=[{"criterion": "c0", "status": "observed"}],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = _evict_observation(ledger)
    assert fresh_observation_count(
        [{"criterion": "c0", "status": "not_visible"}],
        ledger,
        contract_id="c1",
    ) == 1


def test_fold_tier2_observed_survives_eviction() -> None:
    """Fix B long-task: an early observed read evicted from the FIFO window
    still satisfies acceptance tier 2 via its anchor."""
    contract = _single_judge_contract()
    ledger = append_model_observations(
        [],
        contract_id="c1",
        observations=[
            {
                "criterion": "target",
                "status": "observed",
                "observed_value": "x",
            }
        ],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = _evict_observation(ledger)  # evicts the only observed record
    fold = fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id="c1",
        screen_id="s9",
        observation_epoch=9,
        current_step=9,
    )
    assert fold["per_criterion"]["target"]["status"] == "satisfied"
    assert fold["per_criterion"]["target"]["reason"] == "model_observed"
    assert fold["overall"] == "satisfied"


def test_contradicted_anchor_blocks_finish() -> None:
    """Fix B: the anchor reflects the newest read — a final contradicted read
    blocks the finish even when the positive read was long evicted."""
    contract = _single_judge_contract()
    ledger = append_model_observations(
        [],
        contract_id="c1",
        observations=[
            {
                "criterion": "target",
                "status": "observed",
                "observed_value": "x",
            }
        ],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = _evict_observation(ledger)
    ledger = append_model_observations(
        ledger,
        contract_id="c1",
        observations=[{"criterion": "target", "status": "contradicted"}],
        step=9,
        screen_id="s9",
        observation_epoch=9,
    )
    fold = fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id="c1",
        screen_id="s9",
        observation_epoch=9,
        current_step=9,
    )
    assert fold["per_criterion"]["target"]["status"] == "contradicted"
    assert fold["overall"] == "contradicted"
