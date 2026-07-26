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


def test_attach_core_predicates_binds_raw_entity_span_for_vlm_judge() -> None:
    """The expectation must be the raw span, not a hash: fact providers emit
    on-screen text, so a hash expectation is unsatisfiable by construction."""
    criteria = [
        SuccessCriterion("task_completed", "objective visible", "vlm_judge"),
    ]
    migrated = _attach_core_predicates(
        criteria,
        target_app_hint=None,
        ordinal=None,
        entity_span="猫咪视频",
    )
    assert migrated[0].predicate is not None
    assert migrated[0].predicate.predicate_id == "semantic.entity_matches"
    assert migrated[0].predicate.expected_value == "猫咪视频"


def test_attach_core_predicates_binds_raw_accessibility_text() -> None:
    criteria = [
        SuccessCriterion(
            "search_query_visible",
            "村长托马斯",
            "accessibility_text_match",
        ),
    ]

    migrated = _attach_core_predicates(
        criteria,
        target_app_hint=None,
        ordinal=None,
        entity_span=None,
    )

    assert migrated[0].predicate is not None
    assert migrated[0].predicate.predicate_id == "semantic.entity_matches"
    assert migrated[0].predicate.expected_value == "村长托马斯"


def test_attach_core_predicates_leaves_vlm_judge_untyped_without_entities() -> None:
    criteria = [
        SuccessCriterion("task_completed", "objective visible", "vlm_judge"),
    ]
    migrated = _attach_core_predicates(
        criteria,
        target_app_hint=None,
        ordinal=None,
        entity_span=None,
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


def test_pure_fold_unobserved_does_not_override_aggregating_result() -> None:
    """A PureGoalEvaluator fold that could not observe a criterion (fact
    providers produced nothing) must not overwrite an evaluation built on real
    evidence: missing evidence is not contradicting evidence.

    Asserted on the fold itself rather than by grepping node source, so the
    guarantee holds wherever the fold is called from.
    """
    from phone_agent.graph.goal_evaluator import pure_goal_evaluator

    contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩搜索周杰伦")
    names = [item.name for item in contract.success_criteria]

    # Empty ledger: nothing was observed at all.
    evaluation = pure_goal_evaluator.evaluate(
        contract=contract,
        contract_id="cid",
        evidence_ledger=[],
        finish_claim_matched=names,
        screen_id="s1",
        observation_epoch=1,
    )

    per_criterion = evaluation.evidence["per_criterion"]
    assert all(
        item["reason"] == "criterion_unobserved" for item in per_criterion.values()
    )
    # Unobserved is distinct from contradicted, so callers can tell "no
    # evidence" apart from "counter-evidence" and keep the richer verdict.
    assert evaluation.status != "success"
    assert not any(item["status"] == "contradicted" for item in per_criterion.values())


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


# ----------------------------------------------------------------------
# Finish-gate regression: compiled entity contracts must be finishable
# ----------------------------------------------------------------------


def _simulate_finish(task: str, named_evidence: dict[str, dict]):
    """Run AggregatingGoalEvaluator over a heuristic contract as reflect does."""
    from phone_agent.graph.goal_evaluator import AggregatingGoalEvaluator

    contract = HeuristicGoalCompiler().compile(task=task)
    finish_claim = [c.name for c in contract.success_criteria]
    evidence_list = [
        {"criterion": key, **value} for key, value in named_evidence.items()
    ]
    return AggregatingGoalEvaluator().evaluate(
        contract=contract,
        verifier_status="success",
        verifier_evidence={
            "selected_object_signals": {
                "selected_object_match": True,
                "selected_object_expected_rank": contract.ordinal,
            }
        },
        after_observation={"current_app": contract.target_app_hint or "", "top_activity": ""},
        device_signals={"current_app": contract.target_app_hint or ""},
        finish_claim_matched=finish_claim,
        reflect_named_evidence=evidence_list,
        goal_probes=None,
    )


def test_entity_contract_finish_gate_accepts_matching_screen_text() -> None:
    """The semantic predicate lives in the provider's value domain (raw screen
    text), so a screen label containing the task entity satisfies it directly
    rather than needing a self-attestation fallback."""
    result = _simulate_finish(
        "在哔哩哔哩搜索猫咪视频并播放第一个视频",
        {
            "task_completed": {
                "screen_reference": "mark_id=video_title",
                "observed_value": "猫咪视频合集",
                "source": "accessibility",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
            "target_app_visible": {
                "screen_reference": "foreground",
                "observed_value": "bilibili",
                "source": "device",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
            "selected_object_rank": {
                "screen_reference": "item1",
                "observed_value": 1,
                "source": "screen_object",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
        },
    )
    assert result.status == "success", result.evidence.get("per_criterion")


def test_entity_contract_finish_gate_rejects_wrong_entity_on_screen() -> None:
    """Counterpart to the test above: a different entity must contradict.

    While the expectation was hash-bound this case passed via the vlm_judge
    fallback, so the gate accepted finishing on the wrong content.
    """
    result = _simulate_finish(
        "在哔哩哔哩搜索猫咪视频并播放第一个视频",
        {
            "task_completed": {
                "screen_reference": "mark_id=video_title",
                "observed_value": "狗狗视频合集",
                "source": "accessibility",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
            "target_app_visible": {
                "screen_reference": "foreground",
                "observed_value": "bilibili",
                "source": "device",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
            "selected_object_rank": {
                "screen_reference": "item1",
                "observed_value": 1,
                "source": "screen_object",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
        },
    )
    assert result.status == "failure"
    assert result.evidence["per_criterion"]["task_completed"]["reason"] == (
        "typed_contradiction"
    )


def test_entity_span_strips_conjunction_but_keeps_real_app_names() -> None:
    """Conjunctions are trimmed at span edges, never split on: 和/并 occur
    inside real app names (和平精英, 并读新闻)."""
    from phone_agent.graph.goal_compiler import _primary_entity_span

    assert _primary_entity_span("在哔哩哔哩搜索猫咪视频并播放第一个视频") == "猫咪视频"
    assert _primary_entity_span("打开和平精英") == "和平精英"
    assert _primary_entity_span("打开并读新闻") == "并读新闻"


def test_toggle_task_compiles_programmatic_toggle_criterion() -> None:
    """Toggle tasks used to die at the adequacy gate: ui.toggle_state was
    reachable by providers but no verification kind could ever attach it."""
    for task, expected in (("关闭蓝牙", False), ("开启wifi", True)):
        requirements = TaskRequirementExtractor().extract(task)
        contract = HeuristicGoalCompiler().compile(task=task)
        toggle = next(
            item
            for item in contract.success_criteria
            if item.verification == "toggle_state_match"
        )
        assert toggle.predicate is not None
        assert toggle.predicate.predicate_id == "ui.toggle_state"
        assert toggle.predicate.expected_value is expected
        adequacy = ContractAdequacyValidator().validate(requirements, contract)
        assert adequacy.status == "adequate", adequacy.reason_codes


def test_neutral_toggle_verb_asserts_no_target_state() -> None:
    """"切换" names a flip, not a target state, so no state is asserted."""
    from phone_agent.graph.goal_requirements import parse_toggle_intent

    assert parse_toggle_intent("切换飞行模式") is None
    contract = HeuristicGoalCompiler().compile(task="切换飞行模式")
    assert not any(
        item.verification == "toggle_state_match"
        for item in contract.success_criteria
    )


def test_toggle_state_evaluation_reads_checkable_nodes() -> None:
    from phone_agent.graph.goal_evaluator import AggregatingGoalEvaluator

    contract = HeuristicGoalCompiler().compile(task="关闭蓝牙")
    criterion = next(
        item
        for item in contract.success_criteria
        if item.verification == "toggle_state_match"
    )
    evaluator = AggregatingGoalEvaluator()

    def observation(checked: bool) -> dict:
        return {
            "screen_structures": [
                {
                    "nodes": {
                        "n1": {
                            "checkable": True,
                            "checked": checked,
                            "visible": True,
                        }
                    }
                }
            ]
        }

    assert evaluator._check_toggle_state(criterion, observation(False))["status"] == (
        "matched"
    )
    assert evaluator._check_toggle_state(criterion, observation(True))["status"] == (
        "contradicted"
    )
    # Absence of a checkable node is not counter-evidence.
    assert evaluator._check_toggle_state(criterion, {})["status"] == "unknown"


def test_valid_verifications_derives_from_verification_kind() -> None:
    """The runtime allowlist and the Literal cannot list different kinds."""
    from typing import get_args

    from phone_agent.graph.goal import VALID_VERIFICATIONS, VerificationKind

    assert VALID_VERIFICATIONS == frozenset(get_args(VerificationKind))
    assert "toggle_state_match" in VALID_VERIFICATIONS


def test_typed_match_still_upgrades_vlm_judge_criterion() -> None:
    """When the typed predicate does match, it wins without vlm_judge."""
    from phone_agent.graph.goal_evaluator import AggregatingGoalEvaluator

    contract = HeuristicGoalCompiler().compile(task="打开微信给张三发消息说你好")
    crit = next(c for c in contract.success_criteria if c.name == "task_completed")
    assert crit.predicate is not None
    result = AggregatingGoalEvaluator()._check_criterion(
        crit,
        contract=contract,
        verifier_status=None,
        verifier_evidence=None,
        after_observation=None,
        device_signals=None,
        finish_matched_set={"task_completed"},
        named_evidence_map={
            "task_completed": {
                "screen_reference": "title",
                "observed_value": crit.predicate.expected_value,
                "source": "accessibility",
                "screen_id": "s1",
                "observation_epoch": 1,
            }
        },
        vlm_not_run=False,
        goal_probes=None,
    )
    assert result["status"] == "matched"


def test_finish_placeholder_reference_still_rejected_with_predicate_attached() -> None:
    result = _simulate_finish(
        "在哔哩哔哩搜索猫咪视频并播放第一个视频",
        {
            "task_completed": {
                "screen_reference": "region-1",
                "observed_value": "猫咪视频合集",
                "source": "visual_region",
                "screen_id": "s1",
                "observation_epoch": 1,
            },
        },
    )
    assert result.status != "success"
    assert result.evidence["per_criterion"]["task_completed"]["status"] == "missing"


def test_reference_id_with_equals_sign_accepted() -> None:
    from phone_agent.graph.predicates import EvidenceReference

    ref = EvidenceReference(
        source_kind="mark",
        reference_id="mark_id=video_title",
        screen_id="s1",
        observation_epoch=1,
    )
    assert ref.reference_id == "mark_id=video_title"


def test_chinese_compound_ordinals() -> None:
    from phone_agent.graph.goal_requirements import parse_chinese_ordinal

    assert parse_chinese_ordinal("播放第二十个视频") == 20
    assert parse_chinese_ordinal("播放第十二个视频") == 12
    assert parse_chinese_ordinal("播放第二十一个视频") == 21
    assert parse_chinese_ordinal("播放第三个视频") == 3
    assert parse_chinese_ordinal("播放视频") is None


def test_compound_ordinal_flows_into_requirements_and_contract() -> None:
    requirements = TaskRequirementExtractor().extract("在哔哩哔哩播放第二十个视频")
    assert requirements.ordinal == 20
    contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩播放第二十个视频")
    assert contract.ordinal == 20


def test_after_goal_routes_takeover_when_retry_policy_requests_it() -> None:
    from phone_agent.graph.edges import after_goal

    state = {
        "finished": False,
        "error": None,
        "goal_contract_status": "failed",
        "retry_policy": "takeover",
    }
    assert after_goal(state) == "takeover"
    state["retry_policy"] = "none"
    assert after_goal(state) == "end"


def test_agent_config_exposes_fact_extractors() -> None:
    from phone_agent.agent import AgentConfig

    config = AgentConfig()
    assert config.visual_fact_extractor is None
    assert config.whole_screen_fact_extractor is None
