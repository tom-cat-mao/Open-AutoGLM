"""Tests for GoalContract, compilers, and goal_node (Phase 1 scaffold)."""

import json
from dataclasses import dataclass

import pytest

from phone_agent.graph.goal import (
    GoalContract,
    SuccessCriterion,
    build_goal_prompt_block,
    ensure_goal_contract,
)
from phone_agent.graph.goal_compiler import (
    ExternalGoalCompiler,
    HeuristicGoalCompiler,
    LLMGoalCompiler,
    compile_goal_contract,
)
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.runtime_goal import RuntimeGoalContext

# ----------------------------------------------------------------------
# Fake model (mirrors test_plan_reflect.py FakeModelClient)
# ----------------------------------------------------------------------


@dataclass
class FakeModelResponse:
    thinking: str
    action: str
    parse_metadata: dict | None = None


class FakeModelClient:
    def __init__(self, response):
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls = 0

    def request(self, messages, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class RaisingModelClient:
    def request(self, messages, **kwargs):
        raise RuntimeError("compiler failure with secret")


# ----------------------------------------------------------------------
# HeuristicGoalCompiler
# ----------------------------------------------------------------------


def test_heuristic_compiler_produces_weak_vlm_judge_contract() -> None:
    contract = HeuristicGoalCompiler().compile(task="去b站看逗比的雀巢的第二个视频")

    assert contract.compile_status == "compiled"
    assert contract.compile_source == "heuristic"
    assert contract.verification_strategy == "vlm_judge_at_finish"
    assert contract.target_app_hint == "bilibili"
    assert contract.ordinal == 2
    assert len(contract.entities_sha) > 0
    assert len(contract.success_criteria) >= 1
    # Deterministic legacy criteria are explicitly migrated to typed predicates.
    for crit in contract.success_criteria:
        assert crit.verification in {
            "vlm_judge",
            "app_or_activity_match",
            "object_rank_match",
        }
    app_criterion = next(
        item for item in contract.success_criteria if item.name == "target_app_visible"
    )
    assert app_criterion.predicate is not None
    assert app_criterion.predicate.predicate_id == "app.foreground_identity"
    rank_criterion = next(
        item
        for item in contract.success_criteria
        if item.name == "selected_object_rank"
    )
    assert rank_criterion.predicate is not None
    assert rank_criterion.predicate.expected_value == 2


def test_heuristic_compiler_never_fails_on_empty_task() -> None:
    contract = HeuristicGoalCompiler().compile(task="")

    assert contract.compile_status == "compiled"
    assert contract.success_criteria  # at least task_completed


def test_heuristic_trace_payload_has_no_raw_entities() -> None:
    contract = HeuristicGoalCompiler().compile(task="去b站看逗比的雀巢的第二个视频")
    trace = contract.to_trace_payload()

    assert "逗比" not in json.dumps(trace, ensure_ascii=False)
    assert "雀巢" not in json.dumps(trace, ensure_ascii=False)
    assert trace[" compile_status".strip()] == "compiled"


# ----------------------------------------------------------------------
# ExternalGoalCompiler
# ----------------------------------------------------------------------


def test_external_compiler_returns_injected_contract() -> None:
    injected = GoalContract(
        task_hash="abc123",
        redacted_objective="test redacted",
        objective_length=10,
        success_criteria=[
            SuccessCriterion(
                name="player_visible",
                description="player on screen",
                verification="accessibility_text_match",
            ),
        ],
        target_app_hint="bilibili",
        ordinal=2,
        verification_strategy="hybrid",
        compile_status="compiled",
        compile_source="external",
    )
    result = ExternalGoalCompiler(injected).compile(task="去b站看第二个视频")

    assert result.compile_status == "user_override"
    assert result.compile_source == "external"
    assert result.success_criteria[0].name == "player_visible"


# ----------------------------------------------------------------------
# LLMGoalCompiler
# ----------------------------------------------------------------------


def test_llm_compiler_parses_valid_structured_output() -> None:
    compiled_json = json.dumps(
        {
            "objective": "在b站看某UP主的第二个视频",
            "success_criteria": [
                {
                    "name": "player_visible",
                    "description": "播放器或详情页可见",
                    "verification": "accessibility_text_match",
                    "required": True,
                },
                {
                    "name": "selected_rank_2",
                    "description": "打开了第2个视频",
                    "verification": "object_rank_match",
                    "required": True,
                },
            ],
            "constraints": [],
            "non_goals": [],
            "target_app_hint": "bilibili",
            "ordinal": 2,
        },
        ensure_ascii=False,
    )
    model = FakeModelClient(FakeModelResponse("", compiled_json))
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=1).compile(
        task="去b站看逗比的雀巢的第二个视频"
    )

    assert contract.compile_status == "compiled"
    assert contract.compile_source == "llm"
    assert contract.compile_attempts == 1
    assert contract.success_criteria[0].name == "player_visible"
    assert contract.success_criteria[0].verification == "accessibility_text_match"
    assert contract.success_criteria[1].verification == "object_rank_match"
    assert contract.ordinal == 2
    assert contract.verification_strategy == "hybrid"
    # Entity-bearing tasks get a synthesized vlm_judge semantic criterion
    names = [item.name for item in contract.success_criteria]
    assert "task_objective_achieved" in names


def test_llm_compiler_retries_on_parse_failure_then_succeeds() -> None:
    bad_response = FakeModelResponse("", "not valid json")
    good_response = FakeModelResponse(
        "",
        json.dumps(
            {
                "objective": "search something",
                "success_criteria": [
                    {
                        "name": "results_visible",
                        "description": "search results",
                        "verification": "vlm_judge",
                        "required": True,
                    },
                ],
                "constraints": [],
                "non_goals": [],
                "target_app_hint": None,
                "ordinal": None,
            }
        ),
    )
    model = FakeModelClient([bad_response, good_response])
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=1).compile(
        task="搜索蓝牙耳机"
    )

    assert contract.compile_status == "compiled"
    assert contract.compile_attempts == 2
    assert model.calls == 2


def test_llm_compiler_returns_failed_after_retry_exhausted() -> None:
    model = FakeModelClient(
        [FakeModelResponse("", "not json"), FakeModelResponse("", "still not json")]
    )
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=1).compile(
        task="test task"
    )

    assert contract.compile_status == "failed"
    assert contract.compile_source == "llm"
    assert contract.compile_attempts == 2
    assert model.calls == 2


def test_llm_compiler_rejects_duplicate_criterion_names() -> None:
    compiled_json = json.dumps(
        {
            "objective": "test",
            "success_criteria": [
                {
                    "name": "dup",
                    "description": "a",
                    "verification": "vlm_judge",
                    "required": True,
                },
                {
                    "name": "dup",
                    "description": "b",
                    "verification": "vlm_judge",
                    "required": True,
                },
            ],
            "constraints": [],
            "non_goals": [],
            "target_app_hint": None,
            "ordinal": None,
        }
    )
    model = FakeModelClient(FakeModelResponse("", compiled_json))
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(task="test")

    assert contract.compile_status == "failed"


def test_llm_compiler_rejects_invalid_verification() -> None:
    compiled_json = json.dumps(
        {
            "objective": "test",
            "success_criteria": [
                {
                    "name": "c1",
                    "description": "a",
                    "verification": "magic_keyword_match",
                    "required": True,
                },
            ],
            "constraints": [],
            "non_goals": [],
            "target_app_hint": None,
            "ordinal": None,
        }
    )
    model = FakeModelClient(FakeModelResponse("", compiled_json))
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(task="test")

    assert contract.compile_status == "failed"


# ----------------------------------------------------------------------
# compile_goal_contract chain
# ----------------------------------------------------------------------


def test_compile_chain_prefers_external_override() -> None:
    injected = GoalContract(
        task_hash="ext",
        redacted_objective="external",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(name="c1", description="d", verification="vlm_judge")
        ],
        compile_status="compiled",
        compile_source="external",
    )
    state = {"task": "search something", "lang": "cn"}
    config = {
        "configurable": {
            "task_goal_contract_override": injected,
            "allow_legacy_goal_override_for_tests": True,
            "model_client": FakeModelClient(FakeModelResponse("", "{}")),
        }
    }

    result = compile_goal_contract(state, config)

    assert result.compile_source == "external"
    assert result.compile_status == "user_override"


def test_compile_chain_falls_back_to_heuristic_on_llm_failure() -> None:
    state = {"task": "打开设置", "lang": "cn"}
    config = {
        "configurable": {"model_client": RaisingModelClient(), "goal_compile_retry": 1}
    }

    result = compile_goal_contract(state, config)

    assert result.compile_status == "compiled"
    assert result.compile_source == "heuristic_fallback"
    assert result.target_app_hint == "settings"


def test_compile_chain_uses_heuristic_when_no_model_client() -> None:
    state = {"task": "打开设置", "lang": "cn"}
    config = {"configurable": {}}

    result = compile_goal_contract(state, config)

    assert result.compile_status == "compiled"
    assert result.compile_source == "heuristic"


# ----------------------------------------------------------------------
# GoalContract serialization
# ----------------------------------------------------------------------


def test_to_prompt_block_lists_criteria_in_cn() -> None:
    contract = GoalContract(
        task_hash="abc",
        redacted_objective="test objective",
        objective_length=14,
        success_criteria=[
            SuccessCriterion(
                name="player_visible",
                description="播放器可见",
                verification="accessibility_text_match",
            ),
            SuccessCriterion(
                name="rank_2", description="第2个视频", verification="object_rank_match"
            ),
        ],
        target_app_hint="bilibili",
        ordinal=2,
        verification_strategy="hybrid",
        compile_status="compiled",
        compile_source="external",
    )
    block = contract.to_prompt_block(lang="cn")

    assert "任务目标契约" in block
    assert "player_visible" in block
    assert "accessibility_text_match" in block
    assert "object_rank_match" in block
    assert "matched_terminal_evidence" in block


def test_to_prompt_block_lists_criteria_in_en() -> None:
    contract = GoalContract(
        task_hash="abc",
        redacted_objective="test",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(name="c1", description="desc", verification="vlm_judge"),
        ],
        verification_strategy="vlm_judge_at_finish",
        compile_status="compiled",
        compile_source="external",
    )
    block = contract.to_prompt_block(lang="en")

    assert "Task Goal Contract" in block
    assert "c1" in block
    assert "vlm_judge" in block


def test_from_dict_round_trips() -> None:
    original = GoalContract(
        task_hash="h",
        redacted_objective="obj",
        objective_length=3,
        success_criteria=[
            SuccessCriterion(
                name="c1", description="d", verification="vlm_judge", required=False
            ),
        ],
        constraints=["con1"],
        non_goals=["ng1"],
        target_app_hint="settings",
        ordinal=None,
        verification_strategy="vlm_judge_at_finish",
        compile_status="compiled",
        compile_source="llm",
        compile_attempts=1,
    )
    data = original.to_dict()
    restored = GoalContract.from_dict(data)

    assert restored.task_hash == original.task_hash
    assert len(restored.success_criteria) == 1
    assert restored.success_criteria[0].name == "c1"
    assert restored.success_criteria[0].required is False
    assert restored.compile_status == "compiled"


def test_ensure_goal_contract_returns_none_for_pending() -> None:
    state = {"goal_contract": {"compile_status": "pending", "task_hash": "x"}}
    assert ensure_goal_contract(state) is None


def test_ensure_goal_contract_returns_contract_for_compiled() -> None:
    state = {
        "goal_contract": GoalContract(
            task_hash="h",
            redacted_objective="obj",
            objective_length=3,
            success_criteria=[
                SuccessCriterion(name="c1", description="d", verification="vlm_judge")
            ],
            compile_status="compiled",
            compile_source="external",
        ).to_dict()
    }
    contract = ensure_goal_contract(
        state,
        {"configurable": {"allow_legacy_goal_state_for_tests": True}},
    )

    assert contract is not None
    assert contract.task_hash == "h"


def test_build_goal_prompt_block_returns_empty_for_uncompiled() -> None:
    state = {"goal_contract": None}
    assert build_goal_prompt_block(state, lang="cn") == ""


# ----------------------------------------------------------------------
# goal_node
# ----------------------------------------------------------------------


def test_goal_node_noop_when_already_compiled() -> None:
    runtime_goal = RuntimeGoalContext()
    initial_state = {
        "task": "打开设置",
        "step_count": 0,
        "lang": "cn",
        "goal_contract_status": "pending",
        "needs_recompile": False,
    }
    config = {"configurable": {"runtime_goal_context": runtime_goal}}
    compiled = goal_node(initial_state, config)
    state = {**initial_state, **compiled}

    result = goal_node(state, config)

    assert result == {}


def test_goal_node_compiles_on_pending_status() -> None:
    state = {
        "task": "打开设置",
        "step_count": 0,
        "lang": "cn",
        "goal_contract_status": "pending",
    }
    config = {"configurable": {"runtime_goal_context": RuntimeGoalContext()}}

    result = goal_node(state, config)

    assert result["goal_contract_status"] == "compiled"
    assert result["goal_compile_source"] == "heuristic"
    assert result["needs_recompile"] is False
    contract_dict = result["goal_contract"]
    assert isinstance(contract_dict, dict)
    assert contract_dict["target_app_hint"] == "settings"


def test_goal_node_fails_closed_when_requirements_need_clarification() -> None:
    """needs_clarification now only fires on genuinely ambiguous requirements.

    operation_unknown no longer blocks compilation (it falls through to the
    LLM/vlm_judge path), so this test injects an ambiguous requirement set
    directly to exercise the fail-closed gate.
    """
    from phone_agent.graph.goal_requirements import (
        TaskRequirementExtractor,
        TaskRequirementSet,
    )

    ambiguous_requirements = TaskRequirementSet(
        task_hash="",
        operation_kind="launch",
        target_entity_hashes=(),
        target_app_identity=None,
        ordinal=None,
        required_terminal_state="target_app_foreground",
        ambiguities=("app_ambiguous",),
    )
    state = {
        "task": "打开 iTunes 或 微信 处理一下",
        "step_count": 0,
        "lang": "cn",
        "goal_contract_status": "pending",
    }

    from unittest.mock import patch

    extractor = TaskRequirementExtractor()
    with patch(
        "phone_agent.graph.nodes.goal_node.TaskRequirementExtractor",
        return_value=extractor,
    ), patch.object(
        extractor, "extract", return_value=ambiguous_requirements
    ):
        result = goal_node(
            state,
            {"configurable": {"runtime_goal_context": RuntimeGoalContext()}},
        )

    assert result["goal_contract_status"] == "failed"
    assert result["finished"] is True
    assert result["failure_cause"] == "needs_goal_clarification"
    assert "app_ambiguous" in result["task_requirement_set"]["ambiguities"]


def test_goal_node_recompiles_when_needs_recompile() -> None:
    state = {
        "task": "打开设置",
        "step_count": 5,
        "lang": "cn",
        "goal_contract_status": "compiled",
        "needs_recompile": True,
        "goal_contract": GoalContract(
            task_hash="old",
            redacted_objective="old",
            objective_length=3,
            success_criteria=[],
            compile_status="compiled",
            compile_source="external",
        ).to_dict(),
    }
    config = {"configurable": {"runtime_goal_context": RuntimeGoalContext()}}

    result = goal_node(state, config)

    assert result["goal_contract_status"] == "compiled"
    assert result["needs_recompile"] is False
    # Compiled a new contract (heuristic since no model_client)
    assert "task_hash" not in result["goal_contract"]
    assert result["goal_contract"]["schema"] == "goal_contract_state_metadata_v1"


def test_goal_node_requires_runtime_context_for_compilation() -> None:
    result = goal_node(
        {
            "task": "打开设置",
            "step_count": 0,
            "lang": "cn",
            "goal_contract_status": "pending",
        },
        {"configurable": {}},
    )

    assert result["goal_contract_status"] == "failed"
    assert result["error_code"] == "goal_contract_invalid"
    assert result["contract_adequacy_reasons"] == ["runtime_goal_context_missing"]


@pytest.mark.parametrize(
    "mutation", ["missing_reference", "wrong_reference", "changed_task", "lost_context"]
)
def test_compiled_goal_reuse_fails_closed_when_runtime_binding_is_lost(
    mutation: str,
) -> None:
    runtime_goal = RuntimeGoalContext()
    config = {"configurable": {"runtime_goal_context": runtime_goal}}
    initial = {
        "task": "打开设置",
        "step_count": 0,
        "lang": "cn",
        "goal_contract_status": "pending",
        "needs_recompile": False,
    }
    compiled = goal_node(initial, config)
    state = {**initial, **compiled}
    if mutation == "missing_reference":
        state["goal_contract"] = {**state["goal_contract"], "runtime_reference": None}
    elif mutation == "wrong_reference":
        state["goal_contract"] = {
            **state["goal_contract"],
            "runtime_reference": "goal-wrong",
        }
    elif mutation == "changed_task":
        state["task"] = "打开浏览器"
    else:
        config = {"configurable": {"runtime_goal_context": RuntimeGoalContext()}}

    result = goal_node(state, config)

    assert result["goal_contract_status"] == "failed"
    assert result["error_code"] == "goal_contract_invalid"
    assert result["contract_adequacy_reasons"] == ["runtime_goal_binding_unavailable"]


def test_ensure_goal_contract_rejects_trace_payload() -> None:
    """Trace-only metadata must never be accepted as an executable contract."""
    contract = GoalContract(
        task_hash="h",
        redacted_objective="real objective",
        objective_length=3,
        success_criteria=[
            SuccessCriterion(name="c1", description="d", verification="vlm_judge")
        ],
        compile_status="compiled",
        compile_source="external",
    )
    trace = contract.to_trace_payload()
    assert trace["schema"] == "goal_contract_trace_metadata_v1"

    state = {"goal_contract": trace}
    assert ensure_goal_contract(state) is None


def test_validator_accepts_finish_with_matched_terminal_evidence() -> None:
    """P0-1: validator must accept matched_terminal_evidence on finish actions."""
    from phone_agent.actions.validator import validate_action

    action = {
        "_metadata": "finish",
        "message": "task done",
        "matched_terminal_evidence": ["criterion1", "criterion2"],
    }
    validated = validate_action(action)
    assert validated["matched_terminal_evidence"] == ["criterion1", "criterion2"]


def test_validator_rejects_non_list_matched_terminal_evidence() -> None:
    from phone_agent.actions.validator import ActionValidationError, validate_action

    with pytest.raises(ActionValidationError):
        validate_action(
            {
                "_metadata": "finish",
                "message": "done",
                "matched_terminal_evidence": "not a list",
            }
        )
