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

    assert result.status == "inadequate"
    assert "target_app_uncovered" in result.reason_codes


def test_adequacy_validator_rejects_untyped_semantic_self_attestation() -> None:
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

    assert result.status == "inadequate"
    assert "semantic_criterion_missing" in result.reason_codes


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

    assert result.status == "inadequate"
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

    assert result.status == "inadequate"
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
    assert result["finished"] is True


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
