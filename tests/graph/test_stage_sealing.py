"""Stage-Sealing Phase B: seal records, eager sealing, authoritative folds,
revocation (positive counter-observation only), recompile semantic-key
inheritance."""

from phone_agent.graph.goal import GoalContract, SuccessCriterion, TaskStage
from phone_agent.graph.goal_evidence import (
    append_model_observations,
    criterion_semantic_key,
    remap_ledger_for_contract,
    revoke_seals_on_contradiction,
    seal_records_for_contract,
    seal_satisfied_stages,
    sealed_criteria,
    stage_status_from_ledger,
)
from phone_agent.graph.goal_evaluator import PureGoalEvaluator
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


def _criteria() -> list[SuccessCriterion]:
    return [
        SuccessCriterion(
            "date",
            "departure date 2026年10月1日",
            "vlm_judge",
            predicate=CORE_PREDICATE_CATALOG.create_spec(
                "semantic.entity_matches", "2026年10月1日"
            ),
            freshness="trajectory",
        ),
        SuccessCriterion(
            "time",
            "departure window 06:00-12:00",
            "vlm_judge",
            predicate=CORE_PREDICATE_CATALOG.create_spec(
                "semantic.entity_matches", "06:00-12:00"
            ),
            freshness="trajectory",
        ),
        SuccessCriterion("terminal_check", "results page rendered", "vlm_judge"),
    ]


def _contract() -> GoalContract:
    return GoalContract(
        task_hash="c1",
        redacted_objective="book flight",
        objective_length=10,
        success_criteria=_criteria(),
        task_plan=(
            TaskStage("S1", "select date", ("date",), "", 0),
            TaskStage("S2", "apply filter", ("time",), "", 1),
        ),
        compile_status="compiled",
    )


def _observed_ledger(contract: GoalContract) -> list[dict]:
    """Model screen-reads: date observed at epoch 3, time at epoch 5."""

    ledger: list[dict] = []
    for criterion_id, epoch in (("date", 3), ("time", 5)):
        ledger = append_model_observations(
            ledger,
            contract_id=contract.task_hash,
            observations=[
                {"criterion": criterion_id, "status": "observed", "observed_value": "ok"}
            ],
            step=epoch,
            screen_id=f"screen-{epoch}",
            observation_epoch=epoch,
            semantic_keys={
                criterion.name: criterion_semantic_key(criterion.description)
                for criterion in contract.success_criteria
            },
        )
    return ledger


def _contradict_criterion(ledger: list[dict], contract: GoalContract, name: str, epoch: int) -> list[dict]:
    return append_model_observations(
        ledger,
        contract_id=contract.task_hash,
        observations=[
            {"criterion": name, "status": "contradicted", "observed_value": "other"}
        ],
        step=epoch,
        screen_id=f"screen-{epoch}",
        observation_epoch=epoch,
        semantic_keys={
            criterion.name: criterion_semantic_key(criterion.description)
            for criterion in contract.success_criteria
        },
    )


# ----------------------------------------------------------------------
# Eager sealing
# ----------------------------------------------------------------------


def test_seal_triggers_when_all_stage_criteria_latched() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)

    new_ledger, new_seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )

    assert [seal["stage_id"] for seal in new_seals] == ["S1", "S2"]
    seal = new_seals[0]
    assert seal["kind"] == "stage_seal"
    assert seal["criteria_sealed"] == ["date"]
    assert seal["semantic_key"]
    assert seal["screen_id"] == "screen-9"
    assert seal["step"] == 9
    assert seal["sealed_at"] == 9
    assert len([e for e in new_ledger if e.get("kind") == "stage_seal"]) == 2


def test_seal_is_idempotent_per_semantic_key() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)

    ledger, first = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )
    ledger, second = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-10",
        step=10,
    )

    assert len(first) == 2
    assert second == []
    assert len([e for e in ledger if e.get("kind") == "stage_seal"]) == 2


def test_unsealed_stage_can_reseal_after_latching_again() -> None:
    from phone_agent.graph.goal_evidence import criterion_semantic_key

    contract = _contract()
    ledger = _observed_ledger(contract)
    ledger, _ = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )
    # Positive counter-observation unlocks the evidence latch AND the seal.
    ledger = _contradict_criterion(ledger, contract, "date", 10)
    ledger = revoke_seals_on_contradiction(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        contradicted_criteria={"date"},
        screen_id="screen-10",
        step=10,
    )
    # While the contradiction stands, S1 is open and does NOT re-seal.
    ledger, new_seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-11",
        step=11,
    )
    assert new_seals == []
    # A later trusted observed re-read re-latches; eager sealing re-arms S1.
    ledger = append_model_observations(
        ledger,
        contract_id=contract.task_hash,
        observations=[
            {"criterion": "date", "status": "observed", "observed_value": "ok"}
        ],
        step=12,
        screen_id="screen-12",
        observation_epoch=12,
        semantic_keys={
            criterion.name: criterion_semantic_key(criterion.description)
            for criterion in contract.success_criteria
        },
    )
    ledger, new_seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-12",
        step=12,
    )
    assert [seal["stage_id"] for seal in new_seals] == ["S1"]
    # One revoked S1 seal + fresh S1 seal + untouched S2 seal: history
    # accumulates, but only 2 stages are ACTIVE.
    assert len([e for e in ledger if e.get("kind") == "stage_seal"]) == 3
    records = seal_records_for_contract(
        ledger, contract=contract, contract_id=contract.task_hash
    )
    assert {record["stage_id"] for record in records} == {"S1", "S2"}
    s1_record = next(r for r in records if r["stage_id"] == "S1")
    assert s1_record["step"] == 12  # the re-seal is the current S1 authority


# ----------------------------------------------------------------------
# Authoritative fold: sealed criteria pass without claim or current screen
# ----------------------------------------------------------------------


def test_sealed_criteria_are_authoritative_in_pure_fold() -> None:
    contract = _contract()
    staged_only = GoalContract(
        task_hash=contract.task_hash,
        redacted_objective=contract.redacted_objective,
        objective_length=10,
        success_criteria=contract.success_criteria[:2],
        task_plan=contract.task_plan,
        compile_status="compiled",
    )
    ledger = _observed_ledger(contract)
    ledger, _ = seal_satisfied_stages(
        ledger,
        contract=staged_only,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )

    # Empty claim + a different current screen: sealed criteria still pass.
    result = PureGoalEvaluator().evaluate(
        contract=staged_only,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=[],
        screen_id="totally-different-screen",
        observation_epoch=99,
    )
    assert result.status == "success"
    assert result.matched == ["date", "time"]
    assert result.evidence["per_criterion"]["date"]["reason"] == "sealed_by_stage"


def test_seal_record_surfaces_evidence_refs() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)
    ledger, new_seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
        evidence_refs=["screen-9", "step:9"],
    )
    records = seal_records_for_contract(
        ledger, contract=contract, contract_id=contract.task_hash
    )
    assert {record["stage_id"] for record in records} == {"S1", "S2"}
    assert all(record["evidence_refs"] == ["screen-9", "step:9"] for record in records)


# ----------------------------------------------------------------------
# Revocation: only positive counter-observation, never absence
# ----------------------------------------------------------------------


def test_revocation_writes_unseal_only_on_contradiction() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)
    ledger, _ = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )
    # Absence (unknown) must NOT revoke the seal.
    ledger = revoke_seals_on_contradiction(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        contradicted_criteria={"date"},  # only a real contradiction is passed here
        screen_id="screen-10",
        step=10,
    )
    assert len([e for e in ledger if e.get("kind") == "stage_unseal"]) == 1
    assert sealed_criteria(ledger, contract=contract, contract_id=contract.task_hash) == {
        "time"
    }

    # unknown/absence never reaches the revocation function as a contradiction;
    # calling with an empty set must be a no-op.
    before = [e for e in ledger if e.get("kind") == "stage_unseal"]
    after = revoke_seals_on_contradiction(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        contradicted_criteria=set(),
        screen_id="screen-10",
        step=10,
    )
    assert [e for e in after if e.get("kind") == "stage_unseal"] == before


def test_contradicted_evidence_unlocks_stage_status() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)
    ledger, _ = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )
    # A REAL positive counter-observation: contradicted evidence entry + unseal.
    ledger = _contradict_criterion(ledger, contract, "time", 10)
    ledger = revoke_seals_on_contradiction(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        contradicted_criteria={"time"},
        screen_id="screen-10",
        step=10,
    )
    folded = stage_status_from_ledger(
        ledger,
        contract.task_plan,
        contract_id=contract.task_hash,
        criteria={c.name: c for c in contract.success_criteria},
    )
    per_stage = {item["stage_id"]: item for item in folded["per_stage"]}
    assert per_stage["S2"]["status"] == "pending"
    assert per_stage["S1"]["status"] == "satisfied"


# ----------------------------------------------------------------------
# Recompile inheritance: rename criteria, keep evidence
# ----------------------------------------------------------------------


def test_remap_renames_criterion_entries_by_semantic_key() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)

    renamed = GoalContract(
        task_hash=contract.task_hash,
        redacted_objective=contract.redacted_objective,
        objective_length=10,
        success_criteria=[
            SuccessCriterion(
                "departure_date",
                "departure date 2026年10月1日",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "2026年10月1日"
                ),
                freshness="trajectory",
            ),
            SuccessCriterion(
                "time_window",
                "departure window 06:00-12:00",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "06:00-12:00"
                ),
                freshness="trajectory",
            ),
            SuccessCriterion("terminal_check", "results page rendered", "vlm_judge"),
        ],
        task_plan=(
            TaskStage("S1", "select date", ("departure_date",), "", 0),
            TaskStage("S2", "apply filter", ("time_window",), "", 1),
        ),
        compile_status="compiled",
    )

    remapped = remap_ledger_for_contract(
        ledger,
        contract_id=contract.task_hash,
        criteria={c.name: c for c in renamed.success_criteria},
        task_plan=renamed.task_plan,
    )
    observations = [
        e["criterion"] for e in remapped if e.get("kind") == "model_observation"
    ]
    assert observations == ["departure_date", "time_window"]

    folded = stage_status_from_ledger(
        remapped,
        renamed.task_plan,
        contract_id=contract.task_hash,
        criteria={c.name: c for c in renamed.success_criteria},
    )
    per_stage = {item["stage_id"]: item for item in folded["per_stage"]}
    assert per_stage["S1"]["status"] == "satisfied"
    assert per_stage["S2"]["status"] == "satisfied"


def test_seal_inherits_across_renamed_criteria() -> None:
    contract = _contract()
    ledger = _observed_ledger(contract)
    ledger, _ = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id=contract.task_hash,
        screen_id="screen-9",
        step=9,
    )

    renamed = GoalContract(
        task_hash=contract.task_hash,
        redacted_objective=contract.redacted_objective,
        objective_length=10,
        success_criteria=[
            SuccessCriterion(
                "departure_date",
                "departure date 2026年10月1日",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "2026年10月1日"
                ),
                freshness="trajectory",
            ),
            SuccessCriterion(
                "time_window",
                "departure window 06:00-12:00",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "06:00-12:00"
                ),
                freshness="trajectory",
            ),
        ],
        task_plan=(
            TaskStage("S1", "select date", ("departure_date",), "", 0),
            TaskStage("S2", "apply filter", ("time_window",), "", 1),
        ),
        compile_status="compiled",
    )

    # The seal resolves through the CURRENT plan: renamed criteria are sealed.
    assert sealed_criteria(ledger, contract=renamed, contract_id=contract.task_hash) == {
        "departure_date",
        "time_window",
    }
    result = PureGoalEvaluator().evaluate(
        contract=renamed,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=[],
        screen_id="other-screen",
        observation_epoch=99,
    )
    assert result.status == "success"
    assert result.matched == ["departure_date", "time_window"]


def test_remap_is_identity_without_semantic_keys() -> None:
    contract = _contract()
    ledger = [
        {
            "criterion_id": "date",
            "predicate_id": "semantic.entity_matches",
            "status": "matched",
            "reason_code": "existential_match",
            "source_kind": "accessibility",
            "confidence_bucket": "high",
            "contract_id": contract.task_hash,
            "screen_id": "screen-3",
            "observation_epoch": 3,
            "target_app_entered": True,
        }
    ]
    remapped = remap_ledger_for_contract(
        ledger,
        contract_id=contract.task_hash,
        criteria={c.name: c for c in contract.success_criteria},
        task_plan=contract.task_plan,
    )
    assert remapped == ledger
