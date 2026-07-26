import pytest

from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
)
from phone_agent.graph.goal_compiler import (
    GoalCompilationError,
    HeuristicGoalCompiler,
    compile_goal_contract,
)
from phone_agent.graph.edges import after_goal
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG
from phone_agent.graph.runtime_goal import RuntimeGoalContext


def test_requirement_extraction_is_task_bound_and_trace_safe() -> None:
    requirements = TaskRequirementExtractor().extract(
        "在设置里搜索 private@example.com"
    )
    projection = requirements.safe_projection()

    assert requirements.operation_kind == "search"
    assert requirements.target_app_identity == "settings"
    assert "private@example.com" not in str(projection)
    assert projection["target_entity_count"] > 0
    assert "task_hash" not in projection
    assert "target_entity_hashes" not in projection


def test_adequacy_validator_rejects_candidate_contract_self_attestation() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="done",
        objective_length=4,
        success_criteria=[SuccessCriterion("done", "candidate says done", "vlm_judge")],
        target_app_hint=None,
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "degraded"
    assert "target_app_uncovered" in result.reason_codes


def test_adequacy_validator_rejects_untyped_semantic_self_attestation() -> None:
    """Entity-bearing tasks still need semantic coverage with no vlm_judge.

    After the adequacy relaxation, a required vlm_judge criterion counts as
    semantic fallback coverage; a contract with NO vlm_judge and NO typed
    semantic predicate must still be rejected.
    """
    requirements = TaskRequirementExtractor().extract("在设置里搜索 Silverstone")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=18,
        success_criteria=[
            SuccessCriterion("done", "app foreground", "app_or_activity_match")
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    # Semantic gaps are keyword-derived suspicions: still reported, but they
    # degrade the contract rather than terminating the task at step 0.
    assert result.status == "degraded"
    assert "semantic_criterion_missing" in result.reason_codes


def test_adequacy_validator_accepts_vlm_judge_semantic_fallback() -> None:
    """A required vlm_judge criterion satisfies semantic coverage (fallback)."""
    requirements = TaskRequirementExtractor().extract("在设置里搜索 Silverstone")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=18,
        success_criteria=[SuccessCriterion("done", "target visible", "vlm_judge")],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "adequate"


def test_adequacy_validator_accepts_typed_semantic_criterion() -> None:
    requirements = TaskRequirementExtractor().extract("在设置里搜索 Silverstone")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=18,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "Silverstone"
                ),
            )
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "adequate"


def test_adequacy_validator_requires_ordinal_coverage() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置里的第2个项目")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open item",
        objective_length=10,
        success_criteria=[SuccessCriterion("item", "item visible", "vlm_judge")],
        target_app_hint="settings",
        ordinal=None,
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "degraded"
    assert "ordinal_uncovered" in result.reason_codes


def test_requirement_constraints_and_terminal_state_must_be_covered() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置，不要修改任何开关")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open settings",
        objective_length=12,
        success_criteria=[
            SuccessCriterion(
                "app",
                "settings foreground",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    missing = ContractAdequacyValidator().validate(requirements, candidate)
    covered = ContractAdequacyValidator().validate(
        requirements,
        GoalContract(
            **{
                **candidate.__dict__,
                "constraints": ["不要修改任何开关"],
            }
        ),
    )

    assert requirements.constraint_hashes
    assert "constraints_uncovered" in missing.reason_codes
    assert covered.status == "adequate"


def test_terminal_state_requires_operation_appropriate_typed_predicate() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open settings",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "focus",
                "input focused",
                "focus_or_keyboard",
                predicate=CORE_PREDICATE_CATALOG.create_spec("ui.focused", True),
            )
        ],
        target_app_hint="settings",
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "degraded"
    assert "terminal_state_uncovered" in result.reason_codes


def test_production_external_override_requires_independent_requirements() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置")
    contract = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open settings",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "app",
                "settings visible",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        target_app_hint="settings",
        compile_status="compiled",
    )

    with pytest.raises(
        GoalCompilationError, match="independently supplied requirement set"
    ):
        compile_goal_contract(
            {"task": "打开设置", "lang": "cn"},
            {"configurable": {"task_goal_contract_override": contract}},
        )


def test_goal_node_projects_external_override_error_fail_closed() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置")
    contract = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open settings",
        objective_length=4,
        success_criteria=[SuccessCriterion("app", "settings visible", "vlm_judge")],
        target_app_hint="settings",
        compile_status="compiled",
    )

    result = goal_node(
        {"task": "打开设置", "lang": "cn", "goal_contract_status": "pending"},
        {
            "configurable": {
                "task_goal_contract_override": contract,
                "runtime_goal_context": RuntimeGoalContext(),
            }
        },
    )

    assert result["goal_contract_status"] == "failed"
    assert result["error_code"] == "external_goal_requirements_missing"
    # Fail closed: never reaches plan. It routes to human takeover rather than
    # silently ending — previously `finished: True` made that branch dead code.
    assert not result.get("finished")
    assert after_goal(result) == "takeover"


def test_external_override_with_bound_requirements_is_validated() -> None:
    requirements = TaskRequirementExtractor().extract("打开设置")
    contract = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="open settings",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "app",
                "settings visible",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        target_app_hint="settings",
        compile_status="compiled",
    )

    result = compile_goal_contract(
        {"task": "打开设置", "lang": "cn"},
        {
            "configurable": {
                "task_goal_contract_override": contract,
                "task_requirement_set_override": requirements,
            }
        },
    )

    assert result.compile_source == "external"


@pytest.mark.parametrize(
    "task",
    ["Open Chrome", "  OPEN   CHROME  ", "Ｏｐｅｎ　Ｃｈｒｏｍｅ"],
)
def test_task_binding_normalization_is_shared_with_compiler(task: str) -> None:
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)

    assert requirements.task_hash == contract.task_hash
    assert (
        ContractAdequacyValidator().validate(requirements, contract).status
        == "adequate"
    )


# ----------------------------------------------------------------------
# Adequacy severity: structural defects block, semantic gaps degrade
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "关闭蓝牙",
        "开启wifi",
        "切换飞行模式",
        "在美团点一杯咖啡不要加糖",
        "only use wifi to download",
        "打开和平精英",
        "把闹钟设成明天早上七点",
    ],
)
def test_real_tasks_never_die_at_the_adequacy_gate(task: str) -> None:
    """These task families used to be rejected 100% of the time at step 0.

    Toggle tasks demanded ui.toggle_state that no verification could attach,
    and any task containing 不要/only could never match the empty
    contract.constraints. Compilation must now proceed (adequate or degraded).
    """
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)
    result = ContractAdequacyValidator().validate(requirements, contract)

    assert result.status in {"adequate", "degraded"}, result.reason_codes


def test_constraint_clauses_are_covered_by_the_compiler() -> None:
    """Requirement and contract constraints come from one function, so the
    constrained-task deadlock cannot reappear."""
    task = "在美团点一杯咖啡不要加糖"
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)

    assert requirements.constraint_hashes
    assert contract.constraints
    result = ContractAdequacyValidator().validate(requirements, contract)
    assert "constraints_uncovered" not in result.reason_codes


def test_unobservable_predicate_is_a_structural_defect() -> None:
    """A predicate no provider emits can never be satisfied, so it blocks."""
    requirements = TaskRequirementExtractor().extract("在设置里搜索 Silverstone")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=18,
        success_criteria=[
            SuccessCriterion(
                "value",
                "value visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "ui.value_equals", "Silverstone"
                ),
            )
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "inadequate"
    assert "predicate_unobservable" in result.reason_codes


def test_domain_mismatched_expectation_is_a_structural_defect() -> None:
    """The Phase 1 regression, now caught at compile time: a digest
    expectation on a raw-text predicate can never equal provider output."""
    requirements = TaskRequirementExtractor().extract("在设置里搜索 Silverstone")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=18,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "d1d51d7a7c5c"
                ),
            )
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "inadequate"
    assert "predicate_domain_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    "prose_builder",
    [
        lambda: "The screen visibly shows " + "requested " * 5 + "result.",
        lambda: "页面显示" + ("x" * 48),
        lambda: 'Visible text is "' + "generated target" + '"',
    ],
)
def test_prose_shaped_raw_text_binding_is_unobservable(prose_builder) -> None:
    expected_value = prose_builder()
    requirements = TaskRequirementExtractor().extract("在设置里搜索 generated target")
    candidate = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective="search target",
        objective_length=32,
        success_criteria=[
            SuccessCriterion(
                "topic",
                "target visible",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", expected_value
                ),
            )
        ],
        target_app_hint="settings",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
    )

    result = ContractAdequacyValidator().validate(requirements, candidate)

    assert result.status == "inadequate"
    assert "predicate_unobservable" in result.reason_codes


def test_degraded_contract_still_compiles_and_reaches_plan() -> None:
    """A semantic gap records itself but must not terminate the task."""
    state = {
        "task": "切换飞行模式",
        "step_count": 0,
        "lang": "cn",
        "goal_contract_status": "pending",
    }

    result = goal_node(
        state, {"configurable": {"runtime_goal_context": RuntimeGoalContext()}}
    )

    assert result["goal_contract_status"] == "compiled"
    assert result["contract_adequacy_status"] == "degraded"
    assert result["contract_adequacy_reasons"]
    assert after_goal(result) == "plan"
