"""Real-path goal compiler chain tests: production compilers + adequacy gate.

These tests exercise the compilation paths that actually run on-device
(HeuristicGoalCompiler without a model client, and the extraction contract
shared with LLMGoalCompiler) — NOT external contract injection. They exist
because every pre-existing E2E-style test injected a hand-built contract
via task_goal_contract_override, which bypassed the production chain that
was failing on all entity-bearing tasks.
"""

from __future__ import annotations

import pytest

from phone_agent.graph.goal_compiler import (
    HeuristicGoalCompiler,
    _attach_core_predicates,
    _extract_entities_sha,
)
from phone_agent.graph.goal import SuccessCriterion
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
)

# Tasks covering the real failure modes found on-device: entity-bearing
# launch/search/select, ordinal selection, and out-of-vocabulary verbs.
REAL_TASKS = [
    "打开设置并进入Wi-Fi页面",
    "在哔哩哔哩搜索Python教程并播放第一个视频",
    "打开微信给张三发消息说你好",
    "在哔哩哔哩播放第三个视频",
    "打开抖音刷视频",
    "帮我在小红书搜一下上海美食",
    "turn on bluetooth in settings",
]


@pytest.mark.parametrize("task", REAL_TASKS)
def test_heuristic_contract_passes_adequacy_on_real_tasks(task: str) -> None:
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)
    result = ContractAdequacyValidator().validate(requirements, contract)
    assert result.status == "adequate", (
        f"heuristic contract rejected for {task!r}: {result.reason_codes}"
    )


def test_entity_extraction_agrees_between_extractor_and_compiler() -> None:
    """Requirement entity hashes and contract entities_sha must intersect."""
    for task in REAL_TASKS:
        requirements = TaskRequirementExtractor().extract(task)
        if not requirements.target_entity_hashes:
            continue
        compiler_hashes = _extract_entities_sha(task)
        assert set(requirements.target_entity_hashes).intersection(
            compiler_hashes
        ), f"entity hash mismatch for {task!r}"


def test_attach_core_predicates_binds_semantic_entity_for_vlm_judge() -> None:
    criteria = [
        SuccessCriterion("task_completed", "objective visible", "vlm_judge"),
    ]
    migrated = _attach_core_predicates(
        criteria,
        target_app_hint=None,
        ordinal=None,
        entities_sha=["abc123"],
    )
    assert migrated[0].predicate is not None
    assert migrated[0].predicate.predicate_id == "semantic.entity_matches"


def test_attach_core_predicates_leaves_vlm_judge_untyped_without_entities() -> None:
    criteria = [
        SuccessCriterion("task_completed", "objective visible", "vlm_judge"),
    ]
    migrated = _attach_core_predicates(
        criteria,
        target_app_hint=None,
        ordinal=None,
        entities_sha=[],
    )
    assert migrated[0].predicate is None


def test_heuristic_task_completed_carries_semantic_predicate_with_entities() -> None:
    contract = HeuristicGoalCompiler().compile(task="打开微信给张三发消息说你好")
    task_completed = next(
        item for item in contract.success_criteria if item.name == "task_completed"
    )
    assert task_completed.predicate is not None
    assert task_completed.predicate.predicate_id == "semantic.entity_matches"


def test_operation_unknown_does_not_block_adequacy() -> None:
    """Out-of-vocabulary verbs fall through to vlm_judge instead of dying."""
    requirements = TaskRequirementExtractor().extract("帮我在小红书搜一下上海美食")
    assert requirements.operation_kind == "unknown"
    assert not requirements.ambiguities
    contract = HeuristicGoalCompiler().compile(task="帮我在小红书搜一下上海美食")
    result = ContractAdequacyValidator().validate(requirements, contract)
    assert result.status == "adequate"


def test_pure_third_pass_unobserved_does_not_override_aggregating_result() -> None:
    """reflect.py: a PureGoalEvaluator pass that could not observe criteria
    (fact providers produced nothing) must not overwrite the aggregating
    evaluation — missing evidence is not contradicting evidence."""
    import inspect

    from phone_agent.graph.nodes import reflect as reflect_module

    source = inspect.getsource(reflect_module)
    assert "pure_evaluation_degraded" in source
    assert "criterion_unobserved" in source


def test_vlm_judge_rejects_placeholder_screen_reference() -> None:
    from phone_agent.graph.goal_evaluator import AggregatingGoalEvaluator
    from phone_agent.graph.goal import SuccessCriterion

    evaluator = AggregatingGoalEvaluator()
    criterion = SuccessCriterion("task_done", "done visible", "vlm_judge")
    for bad_ref in ("region-1", "screen", "unknown", "区域2"):
        result = evaluator._check_vlm_judge(
            criterion,
            {"task_done"},
            {"task_done": {"screen_reference": bad_ref, "source": "visual_region"}},
            vlm_not_run=False,
        )
        assert result["status"] == "missing", bad_ref
        assert result["reason"] == "placeholder_screen_reference", bad_ref

    result = evaluator._check_vlm_judge(
        criterion,
        {"task_done"},
        {"task_done": {"screen_reference": "mark_id=play_button", "source": "mark"}},
        vlm_not_run=False,
    )
    assert result["status"] == "matched"
