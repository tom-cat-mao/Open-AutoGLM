"""W2 task_plan: data model, compilation, ledger fold, credential branch,
prompt blocks, and the stage-stall recompile write point.

Philosophy red line: stages are belief, never authorization — they enter
prompts but never gates. These tests pin that (finish ignores stage status),
plus the fail-closed and privacy semantics of the metadata payloads.
"""

import json
from dataclasses import dataclass

import pytest

from phone_agent.config.policy import STAGE_STALL_RECOMPILE_WINDOWS
from phone_agent.graph.context import (
    build_plan_context_block,
    continuation_credential,
    stage_stall_recompile,
)
from phone_agent.graph.goal import (
    GoalContract,
    SuccessCriterion,
    TaskStage,
    task_plan_validation_errors,
    validate_task_plan,
)
from phone_agent.graph.goal_compiler import (
    ExternalGoalCompiler,
    HeuristicGoalCompiler,
    LLMGoalCompiler,
    compile_goal_contract,
)
from phone_agent.graph.goal_evidence import stage_status_from_ledger
from phone_agent.graph.goal_evaluator import evaluate_finish_claim
from phone_agent.graph.nodes.reflect import reflect_node


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
        raise RuntimeError("compiler failure")


# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------


def _plan_contract() -> GoalContract:
    """A contract whose task_plan references only real criterion names."""
    return GoalContract(
        task_hash="h",
        redacted_objective="在b站看目标视频",
        objective_length=9,
        success_criteria=[
            SuccessCriterion(
                name="target_app_visible",
                description="b站在前台",
                verification="app_or_activity_match",
            ),
            SuccessCriterion(
                name="player_visible",
                description="播放器可见",
                verification="accessibility_text_match",
            ),
            SuccessCriterion(
                name="task_objective_achieved",
                description="目标视频在播放",
                verification="vlm_judge",
            ),
        ],
        compile_status="compiled",
        compile_source="llm",
        task_plan=(
            TaskStage(
                stage_id="open_app",
                objective="打开b站并进入主页",
                done_criteria=("target_app_visible", "player_visible"),
                fallback="重试启动",
                index=0,
            ),
            TaskStage(
                stage_id="find_video",
                objective="找到目标视频",
                done_criteria=("player_visible",),
                fallback="滚动查找",
                index=1,
            ),
            TaskStage(
                stage_id="play",
                objective="开始播放",
                done_criteria=("task_objective_achieved",),
                fallback="点击播放",
                index=2,
            ),
        ),
    )


def _entry(epoch, criterion, status, *, target_app_entered=True) -> dict:
    return {
        "contract_id": "h",
        "criterion_id": criterion,
        "status": status,
        "screen_id": f"s{epoch}",
        "observation_epoch": epoch,
        "target_app_entered": target_app_entered,
    }


# ----------------------------------------------------------------------
# T1: data model + serialization + validation
# ----------------------------------------------------------------------


def test_task_plan_round_trips_through_contract_dict() -> None:
    original = _plan_contract()
    restored = GoalContract.from_dict(original.to_dict())

    assert restored.task_plan is not None
    assert [stage.stage_id for stage in restored.task_plan] == [
        "open_app",
        "find_video",
        "play",
    ]
    assert restored.task_plan[0].done_criteria == (
        "target_app_visible",
        "player_visible",
    )
    assert restored.task_plan[2].index == 2


def test_state_payload_task_plan_is_redacted_metadata() -> None:
    contract = GoalContract(
        task_hash="h",
        redacted_objective="x",
        objective_length=1,
        success_criteria=[
            SuccessCriterion(name="c1", description="d", verification="vlm_judge"),
        ],
        compile_status="compiled",
        task_plan=(
            TaskStage(
                stage_id="s1",
                objective="联系 13800138000 确认订单",
                done_criteria=("c1",),
                fallback="重试",
                index=0,
            ),
        ),
    )
    payload = contract.to_state_payload(runtime_reference="r1")

    plan = payload["task_plan"]
    assert plan[0]["stage_id"] == "s1"
    assert plan[0]["done_criteria"] == ["c1"]
    assert plan[0]["index"] == 0
    # objective is regex-redacted; raw phone number never leaves the runtime ref
    assert "13800138000" not in json.dumps(payload, ensure_ascii=False)
    assert "<redacted>" in plan[0]["objective"]
    # metadata carries no fallback sentence
    assert "fallback" not in plan[0]


def test_trace_payload_task_plan_is_denatured() -> None:
    contract = _plan_contract()
    trace = contract.to_trace_payload()

    plan = trace["task_plan"]
    assert plan[1]["stage_id"] == "find_video"
    assert plan[1]["done_criteria"] == ["player_visible"]
    # no raw fallback/objective leakage beyond redacted objective
    assert "fallback" not in plan[0]


def test_contract_post_init_redacts_stage_objectives_and_fallback() -> None:
    contract = GoalContract(
        task_hash="h",
        redacted_objective="x",
        objective_length=1,
        success_criteria=[
            SuccessCriterion(name="c1", description="d", verification="vlm_judge"),
        ],
        compile_status="compiled",
        task_plan=(
            TaskStage(
                stage_id="s1",
                objective="发邮件到 private@example.com",
                done_criteria=("c1",),
                fallback="联系 13800138000",
                index=0,
            ),
        ),
    )

    assert "private@example.com" not in contract.task_plan[0].objective
    assert "13800138000" not in contract.task_plan[0].fallback


def test_validation_rejects_unknown_done_criterion_name() -> None:
    plan = (
        TaskStage(
            stage_id="s1",
            objective="x",
            done_criteria=("invented_criterion",),
            fallback="",
            index=0,
        ),
    )
    errors = task_plan_validation_errors(
        plan, criterion_names=["c1"], criteria={"c1": SuccessCriterion("c1", "d", "vlm_judge")}
    )

    assert any("unknown_done_criteria" in error for error in errors)
    assert validate_task_plan(plan, criterion_names=["c1"]) is False


def test_from_dict_drops_stages_with_unknown_criteria_names() -> None:
    data = _plan_contract().to_dict()
    data["task_plan"].append(
        {
            "stage_id": "stale",
            "objective": "old stage",
            "done_criteria": ["vanished_criterion"],
            "fallback": "",
            "index": 3,
        }
    )
    restored = GoalContract.from_dict(data)

    assert restored.task_plan is not None
    assert all(stage.stage_id != "stale" for stage in restored.task_plan)


# ----------------------------------------------------------------------
# T2: compilation
# ----------------------------------------------------------------------


def _llm_json(task_plan, *, criteria=None) -> str:
    return json.dumps(
        {
            "objective": "在b站看目标视频",
            "success_criteria": criteria
            or [
                {
                    "name": "target_app_visible",
                    "description": "b站在前台",
                    "verification": "app_or_activity_match",
                    "required": True,
                },
                {
                    "name": "player_visible",
                    "description": "播放器可见",
                    "verification": "accessibility_text_match",
                    "required": True,
                },
                {
                    "name": "task_objective_achieved",
                    "description": "目标视频在播放",
                    "verification": "vlm_judge",
                    "required": True,
                },
            ],
            "constraints": [],
            "non_goals": [],
            "target_app_hint": "bilibili",
            "ordinal": None,
            "task_plan": task_plan,
        },
        ensure_ascii=False,
    )


def test_llm_compiler_produces_valid_task_plan() -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            _llm_json(
                [
                    {"objective": "打开b站", "done_criteria": ["target_app_visible", "player_visible"], "fallback": "重试"},
                    {"objective": "找到目标视频", "done_criteria": ["player_visible"], "fallback": "滚动"},
                    {"objective": "开始播放", "done_criteria": ["task_objective_achieved"], "fallback": "点击"},
                ]
            ),
        )
    )
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(
        task="去b站看目标视频"
    )

    assert contract.compile_status == "compiled"
    assert contract.task_plan is not None
    assert len(contract.task_plan) == 3
    assert contract.task_plan[0].index == 0
    assert contract.task_plan[2].index == 2
    # stage ids auto-derived when the model omits them
    assert contract.task_plan[0].stage_id == "stage_1"


def test_llm_compiler_missing_task_plan_degrades_to_none() -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            _llm_json(None),
        )
    )
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(
        task="去b站看目标视频"
    )

    assert contract.compile_status == "compiled"
    assert contract.task_plan is None


def test_llm_compiler_rejects_unknown_done_criterion_name() -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            _llm_json(
                [
                    {"objective": "打开b站", "done_criteria": ["target_app_visible", "player_visible"], "fallback": ""},
                    {"objective": "找到目标视频", "done_criteria": ["player_visible"], "fallback": ""},
                    {"objective": "开始播放", "done_criteria": ["invented"], "fallback": ""},
                ]
            ),
        )
    )
    contract = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(
        task="去b站看目标视频"
    )

    assert contract.compile_status == "failed"


def test_llm_compiler_rejects_trivial_only_stage() -> None:
    """A stage whose done criteria are ALL always-true auto standards is rejected."""
    plan = (
        TaskStage(
            stage_id="s1",
            objective="x",
            done_criteria=("target_app_visible",),
            fallback="",
            index=0,
        ),
    )
    contract = _plan_contract()
    errors = task_plan_validation_errors(
        plan,
        criterion_names=[item.name for item in contract.success_criteria],
        criteria={item.name: item for item in contract.success_criteria},
    )
    assert any("trivial_only_done_criteria" in error for error in errors)

    model = FakeModelClient(
        FakeModelResponse(
            "",
            _llm_json(
                [
                    {"objective": "打开b站", "done_criteria": ["target_app_visible"], "fallback": ""},
                    {"objective": "找到目标视频", "done_criteria": ["player_visible"], "fallback": ""},
                    {"objective": "开始播放", "done_criteria": ["task_objective_achieved"], "fallback": ""},
                ]
            ),
        )
    )
    failed = LLMGoalCompiler(model, lang="cn", retry_limit=0).compile(
        task="去b站看目标视频"
    )
    assert failed.compile_status == "failed"


def test_compile_chain_invalid_plan_falls_back_to_heuristic() -> None:
    state = {"task": "去b站看目标视频", "lang": "cn"}
    config = {
        "configurable": {
            "model_client": FakeModelClient(
                FakeModelResponse(
                    "",
                    _llm_json(
                        [
                            {"objective": "打开b站", "done_criteria": ["target_app_visible", "player_visible"], "fallback": ""},
                            {"objective": "找到目标视频", "done_criteria": ["player_visible"], "fallback": ""},
                            {"objective": "开始播放", "done_criteria": ["invented"], "fallback": ""},
                        ]
                    ),
                )
            ),
            "goal_compile_retry": 0,
        }
    }

    result = compile_goal_contract(state, config)

    assert result.compile_status == "compiled"
    assert result.compile_source == "heuristic_fallback"
    assert result.task_plan is None


def test_heuristic_compiler_has_no_task_plan() -> None:
    contract = HeuristicGoalCompiler().compile(task="去b站看第二个视频")
    assert contract.task_plan is None


def test_external_compiler_adopts_valid_plan() -> None:
    contract = _plan_contract()
    result = ExternalGoalCompiler(contract).compile(task="去b站看目标视频")

    assert result.task_plan is not None
    assert result.task_plan[0].stage_id == "open_app"


def test_external_compiler_strips_invalid_plan() -> None:
    contract = GoalContract(
        task_hash="h",
        redacted_objective="x",
        objective_length=1,
        success_criteria=[
            SuccessCriterion(name="c1", description="d", verification="vlm_judge"),
        ],
        compile_status="compiled",
        compile_source="external",
        task_plan=(
            TaskStage(
                stage_id="s1",
                objective="x",
                done_criteria=("missing_criterion",),
                fallback="",
                index=0,
            ),
        ),
    )
    result = ExternalGoalCompiler(contract).compile(task="x")

    assert result.task_plan is None
    assert result.compile_source == "external"


# ----------------------------------------------------------------------
# T3: stage_status_from_ledger (pure fold, ever_matched semantics)
# ----------------------------------------------------------------------


def test_stage_status_all_pending() -> None:
    status = stage_status_from_ledger([], _plan_contract().task_plan, contract_id="h")

    assert status["current_stage_index"] == 0
    assert [stage["status"] for stage in status["per_stage"]] == [
        "pending",
        "pending",
        "pending",
    ]


def test_stage_status_partial_satisfied() -> None:
    """player_visible gates both stage 0 and stage 1, so matching the two
    observed signals satisfies the first two stages: current = stage 2."""
    ledger = [
        _entry(1, "target_app_visible", "matched"),
        _entry(1, "player_visible", "matched"),
    ]
    status = stage_status_from_ledger(ledger, _plan_contract().task_plan, contract_id="h")

    assert status["per_stage"][0]["status"] == "satisfied"
    assert status["per_stage"][1]["status"] == "satisfied"
    assert status["per_stage"][2]["status"] == "pending"
    assert status["current_stage_index"] == 2


def test_stage_status_all_satisfied_current_none() -> None:
    ledger = [
        _entry(1, "target_app_visible", "matched"),
        _entry(1, "player_visible", "matched"),
        _entry(1, "task_objective_achieved", "matched"),
    ]
    status = stage_status_from_ledger(ledger, _plan_contract().task_plan, contract_id="h")

    assert status["current_stage_index"] is None
    assert all(stage["status"] == "satisfied" for stage in status["per_stage"])


def test_stage_status_ever_matched_locks_across_transient_staleness() -> None:
    """A matched observation stays latched across later unknown/stale rounds —
    no oscillation regression (matches the goal_agenda latch semantics)."""
    ledger = [
        _entry(1, "target_app_visible", "matched"),
        _entry(2, "target_app_visible", "unknown"),
        _entry(3, "target_app_visible", "stale"),
    ]
    status = stage_status_from_ledger(ledger, _plan_contract().task_plan, contract_id="h")

    assert status["per_stage"][0]["satisfied_criteria"] == ["target_app_visible"]


def test_stage_status_contradiction_unlocks_latch() -> None:
    ledger = [
        _entry(1, "target_app_visible", "matched"),
        _entry(2, "target_app_visible", "contradicted"),
    ]
    status = stage_status_from_ledger(ledger, _plan_contract().task_plan, contract_id="h")

    assert status["per_stage"][0]["status"] == "pending"
    assert "target_app_visible" in status["per_stage"][0]["pending_criteria"]


def test_stage_status_none_without_plan() -> None:
    assert stage_status_from_ledger([], None, contract_id="h") is None


# ----------------------------------------------------------------------
# T4: continuation_credential stage_advance branch
# ----------------------------------------------------------------------


def _credential_state(**overrides) -> dict:
    state = {
        "goal_contract": {"runtime_reference": "r1", "success_criteria": []},
        "goal_evidence_ledger": [],
        "continuation_last_latch_count": 0,
        "continuation_last_stage_index": None,
        "task_plan_status": None,
        "gui_memory": {"task_progress": {}},
        "finish_validation_evidence": None,
    }
    state.update(overrides)
    return state


def test_stage_advance_branch_grants() -> None:
    credential = continuation_credential(
        _credential_state(
            task_plan_status={"current_stage_index": 2},
            continuation_last_stage_index=1,
        )
    )

    assert credential.granted is True
    assert "stage_advance" in credential.branches


def test_stage_advance_all_satisfied_counts_as_advance() -> None:
    credential = continuation_credential(
        _credential_state(
            task_plan_status={"current_stage_index": None},
            continuation_last_stage_index=2,
        )
    )

    assert credential.granted is True
    assert "stage_advance" in credential.branches


def test_stage_advance_no_plan_is_false_and_does_not_grant() -> None:
    credential = continuation_credential(
        _credential_state(continuation_last_stage_index=1)
    )

    assert credential.granted is False
    assert "stage_advance" not in credential.branches
    assert credential.reason == "no_progress_evidence"


def test_stage_advance_no_boundary_snapshot_is_false() -> None:
    credential = continuation_credential(
        _credential_state(task_plan_status={"current_stage_index": 1})
    )

    assert credential.granted is False
    assert "stage_advance" not in credential.branches


def test_stage_advance_exempts_novelty_negation() -> None:
    """Stage advance is strong typed-progress evidence, like new_latch: it is
    never negated by a high novelty streak."""
    credential = continuation_credential(
        _credential_state(
            task_plan_status={"current_stage_index": 3},
            continuation_last_stage_index=2,
            gui_memory={
                "task_progress": {
                    "trajectory_liveness": "stuck",
                    "novelty_streak": 10,
                }
            },
        )
    )

    assert credential.granted is True
    assert "stage_advance" in credential.branches


def test_stage_advance_combines_with_other_branches_any_grant() -> None:
    ledger = [
        _entry(1, "c1", "unknown"),
        _entry(2, "c1", "matched"),
    ]
    credential = continuation_credential(
        _credential_state(
            task_plan_status={"current_stage_index": 1},
            continuation_last_stage_index=1,
            goal_contract={
                "runtime_reference": "h",
                "success_criteria": [{"name": "c1", "verification": "vlm_judge"}],
            },
            goal_evidence_ledger=ledger,
        )
    )

    # stage did not advance, but criterion_movement grants via the shared fold
    assert credential.granted is True
    assert "stage_advance" not in credential.branches
    assert "criterion_movement" in credential.branches


# ----------------------------------------------------------------------
# T5: static vs dynamic prompt blocks
# ----------------------------------------------------------------------


def test_static_block_contains_full_plan_with_annotation_cn() -> None:
    block = _plan_contract().to_prompt_block(lang="cn")

    assert "任务阶段规划（参考路径，以截图为准）：" in block
    assert "阶段 1/3：打开b站并进入主页" in block
    assert "完成信号：target_app_visible, player_visible" in block
    assert "阶段 3/3：开始播放" in block
    assert "卡住时：重试启动" in block


def test_static_block_annotation_en() -> None:
    block = _plan_contract().to_prompt_block(lang="en")

    assert "task_plan (reference path only; the screenshot prevails):" in block
    assert "stage 1/3: 打开b站并进入主页" in block
    assert "done when: target_app_visible, player_visible" in block


def test_static_block_absent_without_plan() -> None:
    contract = HeuristicGoalCompiler().compile(task="打开设置")
    assert contract.task_plan is None
    assert "任务阶段规划" not in contract.to_prompt_block(lang="cn")
    assert "task_plan" not in contract.to_prompt_block(lang="en")


def test_dynamic_block_carries_current_stage_focus() -> None:
    contract = _plan_contract()
    state = {
        "task": "去b站看目标视频",
        "lang": "cn",
        "goal_contract": contract.to_state_payload(runtime_reference="r1"),
        "task_plan_status": {
            "current_stage_index": 1,
            "per_stage": [
                {
                    "stage_id": "open_app",
                    "index": 0,
                    "status": "satisfied",
                    "satisfied_criteria": ["target_app_visible", "player_visible"],
                    "pending_criteria": [],
                },
                {
                    "stage_id": "find_video",
                    "index": 1,
                    "status": "pending",
                    "satisfied_criteria": [],
                    "pending_criteria": ["player_visible"],
                },
                {
                    "stage_id": "play",
                    "index": 2,
                    "status": "pending",
                    "satisfied_criteria": [],
                    "pending_criteria": ["task_objective_achieved"],
                },
            ],
        },
        "goal_agenda": [],
        "action_parsed": None,
        "action_result": None,
        "failure_memory": [],
        "gui_memory": {},
        "grounding_observation": None,
        "current_app": "bilibili",
        "context_budget": None,
        "max_steps": 10,
        "step_count": 3,
        "locate_count": 0,
        "continuation_count": 0,
        "suggested_strategy": None,
        "reflection_verdict": None,
        "grounding_failure_code": None,
        "summarized_history": "",
        "repeated_failure_count": 0,
        "repeated_action_detected": False,
        "invalidated_mark_ids": [],
        "action_outcome_summary": None,
        "action_ledger": [],
        "screen_belief": {},
    }
    block, metrics = build_plan_context_block(state, lang="cn")

    assert metrics["context_block_chars"] > 0
    assert "task_plan_status:" in block
    assert '"current_stage": "2/3"' in block
    assert "找到目标视频" in block
    assert '"pending_criteria": ["player_visible"]' in block


def test_dynamic_block_absent_without_plan() -> None:
    state = {
        "task": "x",
        "lang": "cn",
        "goal_contract": HeuristicGoalCompiler().compile(task="打开设置").to_state_payload(
            runtime_reference="r1"
        ),
        "task_plan_status": None,
        "goal_agenda": [],
        "action_parsed": None,
        "action_result": None,
        "failure_memory": [],
        "gui_memory": {},
        "grounding_observation": None,
        "current_app": "settings",
        "context_budget": None,
        "max_steps": 10,
        "step_count": 1,
        "locate_count": 0,
        "continuation_count": 0,
        "suggested_strategy": None,
        "reflection_verdict": None,
        "grounding_failure_code": None,
        "summarized_history": "",
        "repeated_failure_count": 0,
        "repeated_action_detected": False,
        "invalidated_mark_ids": [],
        "action_outcome_summary": None,
        "action_ledger": [],
        "screen_belief": {},
    }
    block, metrics = build_plan_context_block(state, lang="cn")

    assert "task_plan_status" not in block


def test_dynamic_focus_never_enters_static_block() -> None:
    static = _plan_contract().to_prompt_block(lang="cn")

    assert "task_plan_status" not in static
    assert "current_stage" not in static
    assert '"2/3"' not in static


# ----------------------------------------------------------------------
# T6: stage-stall recompile write point
# ----------------------------------------------------------------------


def test_stage_stall_recompile_pure_trigger_and_reset() -> None:
    prev = {"current_stage_index": 0}
    stalled = {"current_stage_index": 0}

    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=1,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
    )
    assert windows == 2
    assert recompile is True
    assert grace == 0

    # stage advanced -> streak resets, no recompile
    advanced = {"current_stage_index": 1}
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=advanced,
        liveness_state="stuck",
        stall_windows=3,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 0

    # exploring (not stuck) -> streak resets
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="exploring",
        stall_windows=3,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 0

    # below threshold -> count, no recompile
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=0,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
    )
    assert windows == 1
    assert recompile is False
    assert grace == 0


def test_stage_stall_recompile_no_plan_never_triggers() -> None:
    windows, recompile, grace = stage_stall_recompile(
        previous_status=None,
        current_status=None,
        liveness_state="stuck",
        stall_windows=5,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 0


# ----------------------------------------------------------------------
# P3: post-recompile grace windows are immune; counting restarts after
# ----------------------------------------------------------------------


def _stuck_status() -> dict:
    return {"current_stage_index": 0}


def test_stage_stall_recompile_grace_windows_do_not_count_stall() -> None:
    prev = _stuck_status()
    stalled = _stuck_status()

    # grace=2: the window is immune even with a maxed stall counter.
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=5,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=2,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 1

    # grace=1: still immune; the counter keeps ticking down.
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=5,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=grace,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 0

    # grace expired: the next stuck window counts from zero again.
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=0,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=0,
    )
    assert windows == 1
    assert recompile is False
    assert grace == 0


def test_stage_stall_recompile_grace_cannot_trigger_recompile() -> None:
    prev = _stuck_status()
    stalled = _stuck_status()
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=STAGE_STALL_RECOMPILE_WINDOWS - 1,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=1,
    )
    assert windows == 0
    assert recompile is False
    assert grace == 0


def test_stage_stall_recompile_zero_grace_matches_previous_behavior() -> None:
    prev = _stuck_status()
    stalled = _stuck_status()
    windows, recompile, grace = stage_stall_recompile(
        previous_status=prev,
        current_status=stalled,
        liveness_state="stuck",
        stall_windows=1,
        threshold=STAGE_STALL_RECOMPILE_WINDOWS,
        grace_windows=0,
    )
    assert windows == 2
    assert recompile is True
    assert grace == 0


def test_reflect_sets_needs_recompile_on_stage_stall(base_state, fake_device) -> None:
    """Reflect is the single needs_recompile writer: a plan that neither
    advances for K stuck windows gets flagged so the replan->goal route
    rebuilds it."""
    contract = _plan_contract()
    base_state["goal_contract"] = contract
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["task_plan_status"] = {"current_stage_index": 0, "per_stage": []}
    base_state["stage_stall_windows"] = 1
    # trajectory_liveness=stuck: 4+ repeated (surface, screen) identities with
    # no criterion movement (vlm_judge-only criteria collect no facts).
    base_state["gui_memory"]["screen_transition_stream"] = [
        {"surface": "tv.danmaku.bili/A", "semantic_screen_id": "s", "screen_id": "x"}
    ] * 5
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"go_back","message":"页面不对"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
            }
        },
    )

    assert result["needs_recompile"] is True
    assert result["stage_stall_windows"] == 2
    assert result["task_plan_status"]["current_stage_index"] == 0
    # zero new model calls for the plan fold
    assert model.calls == 1


def test_reflect_does_not_flag_recompile_without_plan(
    base_state, fake_device
) -> None:
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["task_plan_status"] = None
    base_state["stage_stall_windows"] = 2
    base_state["gui_memory"]["screen_transition_stream"] = [
        {"surface": "FakeApp/A", "semantic_screen_id": "s", "screen_id": "x"}
    ] * 4
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
            }
        },
    )

    assert result.get("needs_recompile") is not True
    assert result["stage_stall_windows"] == 0
    assert result["task_plan_status"] is None
    assert model.calls == 1


# ----------------------------------------------------------------------
# Anti-pattern regression: stages never gate finish
# ----------------------------------------------------------------------


def _judge_only_contract() -> GoalContract:
    """A contract whose criteria are all settled by grounded judge evidence,
    so stage status (ledger fold) and finish status (named evidence) can be
    driven independently in the anti-pattern tests."""
    return GoalContract(
        task_hash="h",
        redacted_objective="在b站看目标视频",
        objective_length=9,
        success_criteria=[
            SuccessCriterion(
                name="page_home_visible",
                description="b站首页可见",
                verification="vlm_judge",
            ),
            SuccessCriterion(
                name="player_opened",
                description="播放器已打开",
                verification="vlm_judge",
            ),
            SuccessCriterion(
                name="video_playing",
                description="目标视频正在播放",
                verification="vlm_judge",
            ),
        ],
        compile_status="compiled",
        compile_source="llm",
        task_plan=(
            TaskStage(
                stage_id="home",
                objective="进入b站首页",
                done_criteria=("page_home_visible",),
                fallback="重试启动",
                index=0,
            ),
            TaskStage(
                stage_id="open_player",
                objective="打开目标视频播放器",
                done_criteria=("player_opened",),
                fallback="重新点击",
                index=1,
            ),
            TaskStage(
                stage_id="playing",
                objective="视频开始播放",
                done_criteria=("video_playing",),
                fallback="等待缓冲",
                index=2,
            ),
        ),
    )


def test_finish_gate_ignores_task_plan_stage_status() -> None:
    """Every stage pending, yet a finish claim with grounded judge evidence
    succeeds: the terminal contract is the only finish authority (W2 red line)."""
    contract = _judge_only_contract()
    stage = stage_status_from_ledger([], contract.task_plan, contract_id="h")
    assert stage["current_stage_index"] == 0
    assert all(row["status"] == "pending" for row in stage["per_stage"])

    evidence = [
        {
            "criterion": name,
            "screen_reference": f"mark_id={name}",
            "observed_value": "可见",
        }
        for name in ("page_home_visible", "player_opened", "video_playing")
    ]
    evaluation = evaluate_finish_claim(
        contract=contract,
        verifier_evidence=None,
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=[item["criterion"] for item in evidence],
        reflect_named_evidence=evidence,
    )
    assert evaluation.status == "success"


def test_finish_gate_fails_closed_on_missing_evidence_even_with_stages_done() -> None:
    """The mirror: stages fully satisfied but no terminal evidence still fails
    closed — stage status can never substitute for criterion evidence."""
    contract = _judge_only_contract()
    ledger = [
        _entry(1, "page_home_visible", "matched"),
        _entry(1, "player_opened", "matched"),
        _entry(1, "video_playing", "matched"),
    ]
    stage = stage_status_from_ledger(ledger, contract.task_plan, contract_id="h")
    assert stage["current_stage_index"] is None

    evaluation = evaluate_finish_claim(
        contract=contract,
        verifier_evidence=None,
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=[],
        reflect_named_evidence=None,
    )
    assert evaluation.status != "success"
