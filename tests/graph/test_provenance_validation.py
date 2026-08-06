"""Goal/Plan/Validator provenance unification (L1-L4).

Covers the execution doc `docs/execution-provenance-validation.md`:

* L1 compile sufficiency — `parameter_spans` / `parameter_hashes` /
  `parameter_constraint_uncovered`; multi-fragment `_quoted_spans` →
  `semantic.attributes_present`; compiler prompt pairing.
* L2 provenance semantics — `CriterionSpec.provenance`/`control_hint`; the
  fold decision table (state E2/E3/E4/E5 vs confirmed E4/E5 only); the
  `ui.parameter_value` interval matcher; judge control-binding; the
  `_is_self_observable` mechanical channel; default provenance dispatch.
* L3 sealing gates — action causality (monotonic epoch / launch baseline),
  provenance gate, duplicate done-criteria compile check, key-collision
  smallest-index ownership.
* L4 plan wiring — `criterion_gap_status` + the CN/EN gap-list render.
* Mandatory end-to-end regression: the 160431 携程 residual scenario —
  leftover filter + confirmed time-window criterion → finish blocked with
  feedback guiding to the filter panel; after a precise control read the
  finish passes.
"""

import json
from dataclasses import dataclass

import pytest

from phone_agent.config.prompts_en import CONTEXT_USAGE_RULES as CONTEXT_USAGE_RULES_EN
from phone_agent.config.prompts_zh import (
    CONTEXT_USAGE_RULES as CONTEXT_USAGE_RULES_ZH,
)
from phone_agent.graph.goal_compiler import (
    GOAL_COMPILER_SYSTEM_PROMPT_CN,
    GOAL_COMPILER_SYSTEM_PROMPT_EN,
    HeuristicGoalCompiler,
    LLMGoalCompiler,
    _attach_core_predicates,
    _dispatch_provenance,
    _quoted_spans,
)
from phone_agent.graph.context import build_plan_context_block
from phone_agent.graph.goal import (
    GoalContract,
    SuccessCriterion,
    TaskStage,
    task_plan_validation_errors,
)
from phone_agent.graph.goal_binding import compute_task_binding
from phone_agent.graph.goal_evidence import (
    append_evaluation_entries,
    append_model_observations,
    append_screen_text_digest,
    criterion_gap_status,
    criterion_semantic_key,
    seal_records_for_contract,
    seal_satisfied_stages,
    stage_semantic_key,
)
from phone_agent.graph.goal_evaluator import (
    _is_self_observable,
    fold_acceptance_verdicts,
)
from phone_agent.graph.goal_requirements import (
    STRUCTURAL_REASON_CODES,
    ContractAdequacyValidator,
    TaskRequirementExtractor,
)
from phone_agent.graph.nodes.acceptance import acceptance_node
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    Matcher,
    ObservedFact,
)
from phone_agent.graph.runtime_goal import RuntimeGoalContext


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _semantic_keys(contract: GoalContract) -> dict[str, str]:
    return {
        criterion.name: criterion_semantic_key(criterion.description)
        for criterion in contract.success_criteria
    }


def _append(
    ledger: list[dict],
    contract: GoalContract,
    name: str,
    *,
    epoch: int,
    screen: str,
    source: str = "accessibility",
    predicate_id: str = "semantic.entity_matches",
    status: str = "matched",
    target_app_entered: bool = True,
) -> list[dict]:
    return append_evaluation_entries(
        ledger,
        evaluation={
            "evidence": {
                "per_criterion": {
                    name: {
                        "status": status,
                        "reason": "existential_match",
                        "source": source,
                    }
                }
            }
        },
        contract_id=contract.task_hash,
        screen_id=screen,
        observation_epoch=epoch,
        predicate_ids={name: predicate_id},
        target_app_entered=target_app_entered,
        semantic_keys=_semantic_keys(contract),
    )


def _observe(
    ledger: list[dict],
    contract: GoalContract,
    name: str,
    *,
    status: str = "observed",
    value: str | None = None,
    step: int,
    screen: str,
    epoch: int,
) -> list[dict]:
    """S1/S2 helper: one model screen-read appended to the ledger."""

    return append_model_observations(
        ledger,
        contract_id=contract.task_hash,
        observations=[
            {"criterion": name, "status": status, "observed_value": value}
        ],
        step=step,
        screen_id=screen,
        observation_epoch=epoch,
        semantic_keys=_semantic_keys(contract),
    )


def _residual_contract() -> GoalContract:
    """The 160431 shape: a confirmed time-window criterion + state results."""
    return GoalContract(
        task_hash="c1",
        redacted_objective="查航班",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "time_filter_confirmed",
                "筛选面板显示‘早上6点到12点’时段",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "早上6点到12点"
                ),
                provenance="confirmed",
                control_hint="打开筛选面板读取时段值",
                required=True,
            ),
            SuccessCriterion(
                "flight_results",
                "航班列表卡片",
                "vlm_judge",
                required=True,
            ),
        ],
        task_plan=(
            TaskStage("S1", "应用筛选", ("time_filter_confirmed",), "", 0),
            TaskStage("S2", "结果页", ("flight_results",), "", 1),
        ),
        compile_status="compiled",
        compile_source="external",
    )


def _fold(
    contract: GoalContract,
    ledger: list[dict],
    *,
    screen: str = "results",
    epoch: int = 9,
    claim: list[str] | None = None,
    judge: list[dict] | None = None,
    step: int | None = None,
) -> dict:
    return fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id=contract.task_hash,
        screen_id=screen,
        observation_epoch=epoch,
        finish_claim_matched=claim or [c.name for c in contract.success_criteria],
        judge_verdicts=judge,
        current_step=step,
    )


# ----------------------------------------------------------------------
# L1: compile sufficiency
# ----------------------------------------------------------------------


class TestQuotedSpansBinding:
    def test_multi_fragment_binds_attributes_present(self) -> None:
        criterion = SuccessCriterion(
            "filters",
            '筛选面板同时显示"上海"与"最便宜"',
            "accessibility_text_match",
        )
        migrated = _attach_core_predicates(
            [criterion], target_app_hint=None, ordinal=None, entity_span=None
        )
        predicate = migrated[0].predicate
        assert predicate is not None
        assert predicate.predicate_id == "semantic.attributes_present"
        assert predicate.expected_value == ["上海", "最便宜"]
        # Both fragments must appear in ONE control subtree text.
        assert migrated[0].provenance == "confirmed"

    def test_single_fragment_keeps_entity_matches(self) -> None:
        criterion = SuccessCriterion(
            "target_visible",
            'The screen shows "literal target".',
            "accessibility_text_match",
        )
        migrated = _attach_core_predicates(
            [criterion], target_app_hint=None, ordinal=None, entity_span="fallback"
        )
        assert migrated[0].predicate.predicate_id == "semantic.entity_matches"
        assert migrated[0].predicate.expected_value == "literal target"

    def test_single_interval_fragment_binds_entity_matches(self) -> None:
        """S5: interval literals no longer special-case into ui.parameter_value;
        they bind a raw-text entity expectation like any quoted literal (the
        model reads the value at finish)."""
        criterion = SuccessCriterion(
            "time_filter",
            "筛选面板显示“06:00-12:00”时段",
            "accessibility_text_match",
        )
        migrated = _attach_core_predicates(
            [criterion], target_app_hint=None, ordinal=None, entity_span=None
        )
        predicate = migrated[0].predicate
        assert predicate.predicate_id == "semantic.entity_matches"
        assert predicate.expected_value == "06:00-12:00"

    def test_quoted_spans_returns_all_fragments(self) -> None:
        assert _quoted_spans('显示"上海"与"最便宜"') == ["上海", "最便宜"]
        assert _quoted_spans('显示"上海"') == ["上海"]
        assert _quoted_spans("plain text") == []


class TestProvenanceDispatch:
    def test_default_dispatch_rules(self) -> None:
        criteria = [
            SuccessCriterion(
                "app",
                "app 前台",
                "app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "ctrip"
                ),
            ),
            SuccessCriterion(
                "rank",
                "第3个",
                "object_rank_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec("ui.object_rank", 3),
            ),
            SuccessCriterion(
                "toggle",
                "开关",
                "toggle_state_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec("ui.toggle_state", True),
            ),
            SuccessCriterion(
                "param",
                "筛选面板时段 06:00-12:00",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "06:00-12:00"
                ),
            ),
            SuccessCriterion("plain", "目标可见", "vlm_judge"),
        ]
        dispatched = _dispatch_provenance(criteria)
        by_name = {c.name: c.provenance for c in dispatched}
        assert by_name["app"] == "state"
        assert by_name["rank"] == "confirmed"
        assert by_name["toggle"] == "state"
        assert by_name["param"] == "confirmed"
        assert by_name["plain"] == "state"

    def test_explicit_confirmed_is_kept(self) -> None:
        criteria = [
            SuccessCriterion(
                "p",
                "普通描述",
                "vlm_judge",
                provenance="confirmed",
            )
        ]
        dispatched = _dispatch_provenance(criteria)
        assert dispatched[0].provenance == "confirmed"

    def test_heuristic_compiler_dispatch_rank_to_confirmed(self) -> None:
        contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩播放第三个视频")
        rank = next(
            item
            for item in contract.success_criteria
            if item.name == "selected_object_rank"
        )
        assert rank.provenance == "confirmed"


class TestCompilerPromptPairing:
    def test_compiler_prompts_cn_en_declare_parameter_rules(self) -> None:
        for prompt in (GOAL_COMPILER_SYSTEM_PROMPT_CN, GOAL_COMPILER_SYSTEM_PROMPT_EN):
            assert "confirmed" in prompt
            assert "provenance" in prompt
        assert "显式参数约束必须拥有独立判据" in GOAL_COMPILER_SYSTEM_PROMPT_CN
        assert (
            "Every explicit parameter constraint gets its own criterion"
            in GOAL_COMPILER_SYSTEM_PROMPT_EN
        )
        assert "从结果列表推断" in GOAL_COMPILER_SYSTEM_PROMPT_CN
        assert "deriving it from the result list" in GOAL_COMPILER_SYSTEM_PROMPT_EN


# ----------------------------------------------------------------------
# L2: provenance semantics
# ----------------------------------------------------------------------


class TestCriterionProvenanceField:
    def test_round_trips_through_dict(self) -> None:
        contract = _residual_contract()
        data = contract.to_dict()
        restored = GoalContract.from_dict(data)
        criterion = next(
            item for item in restored.success_criteria if item.name == "time_filter_confirmed"
        )
        assert criterion.provenance == "confirmed"
        assert criterion.control_hint == "打开筛选面板读取时段值"

    def test_prompt_block_renders_confirmation_tag(self) -> None:
        block_cn = _residual_contract().to_prompt_block(lang="cn")
        block_en = _residual_contract().to_prompt_block(lang="en")
        assert "[确认]" in block_cn
        assert "[confirm]" in block_en
        # state criteria carry no tag
        state_block = GoalContract(
            task_hash="c",
            redacted_objective="t",
            objective_length=1,
            success_criteria=[
                SuccessCriterion("r", "结果页", "vlm_judge", provenance="state")
            ],
            compile_status="compiled",
        ).to_prompt_block(lang="cn")
        assert "[确认]" not in state_block

    def test_provenance_in_state_and_trace_payload(self) -> None:
        contract = _residual_contract()
        state_payload = contract.to_state_payload(runtime_reference="r1")
        confirmed = next(
            item
            for item in state_payload["success_criteria"]
            if item["name"] == "time_filter_confirmed"
        )
        assert confirmed["provenance"] == "confirmed"
        trace = contract.to_trace_payload()
        assert trace["success_criteria"][0]["provenance"] == "confirmed"

    def test_control_hint_is_redacted(self) -> None:
        contract = GoalContract(
            task_hash="c",
            redacted_objective="t",
            objective_length=1,
            success_criteria=[
                SuccessCriterion(
                    "p",
                    "描述",
                    "vlm_judge",
                    provenance="confirmed",
                    control_hint="联系 13800138000",
                )
            ],
            compile_status="compiled",
        )
        assert "13800138000" not in (contract.success_criteria[0].control_hint or "")


class TestFoldDecisionTable:
    """Model-delegated fold (S2/S3): satisfied = model screen-read observed /
    judge verdict with a valid reference / programmatic device truth."""

    def test_no_observation_stays_unknown(self) -> None:
        contract = _residual_contract()
        fold = _fold(contract, [])
        assert fold["per_criterion"]["flight_results"]["status"] == "unknown"
        assert fold["overall"] == "unknown"

    def test_model_observed_is_satisfied(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "flight_results", value="航班列表", step=9,
            screen="results", epoch=9,
        )
        ledger = _observe(
            ledger, contract, "time_filter_confirmed", value="早上6点到12点",
            step=9, screen="results", epoch=9,
        )
        fold = _fold(contract, ledger)
        verdict = fold["per_criterion"]["flight_results"]
        assert verdict["status"] == "satisfied"
        assert verdict["reason"] == "model_observed"
        assert fold["overall"] == "satisfied"

    def test_model_not_visible_stays_unknown(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "flight_results", status="not_visible",
            step=9, screen="results", epoch=9,
        )
        fold = _fold(contract, ledger)
        assert fold["per_criterion"]["flight_results"]["status"] == "unknown"

    def test_model_contradicted_blocks(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", status="contradicted",
            value="全天航班", step=9, screen="results", epoch=9,
        )
        fold = _fold(contract, ledger)
        verdict = fold["per_criterion"]["time_filter_confirmed"]
        assert verdict["status"] == "contradicted"
        assert fold["overall"] == "contradicted"

    def test_observed_value_is_redacted_in_ledger(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="联系 13800138000",
            step=9, screen="results", epoch=9,
        )
        fold = _fold(contract, ledger)
        observed = fold["per_criterion"]["time_filter_confirmed"].get(
            "observed_value"
        )
        assert observed is None or "13800138000" not in str(observed)

    def test_judge_satisfied_requires_evidence_step(self) -> None:
        """S3: a satisfied verdict WITHOUT an evidence_step reference degrades
        to unknown (form-only reference validation)."""
        contract = _residual_contract()
        fold = _fold(
            contract,
            [],
            judge=[
                {
                    "criterion": "time_filter_confirmed",
                    "status": "satisfied",
                    "observed_value": "筛选面板显示早上6点到12点",
                },
                {"criterion": "flight_results", "status": "satisfied"},
            ],
            step=9,
        )
        verdict = fold["per_criterion"]["time_filter_confirmed"]
        assert verdict["status"] == "unknown"
        assert verdict["reason"] == "judge_reference_missing_or_out_of_range"

    def test_judge_satisfied_with_final_screen_reference_passes(self) -> None:
        contract = _residual_contract()
        fold = _fold(
            contract,
            [],
            judge=[
                {
                    "criterion": "time_filter_confirmed",
                    "status": "satisfied",
                    "observed_value": "筛选面板显示早上6点到12点",
                    "evidence_step": "final_screen",
                },
                {
                    "criterion": "flight_results",
                    "status": "satisfied",
                    "evidence_step": "final_screen",
                },
            ],
            step=9,
        )
        verdict = fold["per_criterion"]["time_filter_confirmed"]
        assert verdict["status"] == "satisfied"
        assert verdict["reason"] == "judge_verdict"
        assert fold["overall"] == "satisfied"

    def test_judge_satisfied_with_trajectory_step_passes(self) -> None:
        contract = _residual_contract()
        fold = _fold(
            contract,
            [],
            judge=[
                {
                    "criterion": "time_filter_confirmed",
                    "status": "satisfied",
                    "evidence_step": 5,
                },
                {
                    "criterion": "flight_results",
                    "status": "satisfied",
                    "evidence_step": "s9",
                },
            ],
            step=9,
        )
        assert fold["overall"] == "satisfied"

    def test_judge_evidence_step_out_of_range_is_unknown(self) -> None:
        contract = _residual_contract()
        fold = _fold(
            contract,
            [],
            judge=[
                {
                    "criterion": "flight_results",
                    "status": "satisfied",
                    "evidence_step": 99,
                }
            ],
            step=9,
        )
        verdict = fold["per_criterion"]["flight_results"]
        assert verdict["status"] == "unknown"
        assert verdict["reason"] == "judge_reference_missing_or_out_of_range"

    def test_contradiction_overrides(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", status="contradicted",
            value="06:00-14:00", step=9, screen="results", epoch=9,
        )
        fold = _fold(contract, ledger)
        assert fold["per_criterion"]["time_filter_confirmed"]["status"] == "contradicted"
        assert fold["overall"] == "contradicted"


class TestSelfObservable:
    def test_confirmed_typed_criterion_is_mechanical(self) -> None:
        contract = _residual_contract()
        confirmed = next(
            item
            for item in contract.success_criteria
            if item.name == "time_filter_confirmed"
        )
        assert _is_self_observable(confirmed) is True

    def test_state_raw_text_judge_stays_judge(self) -> None:
        state_judge = SuccessCriterion(
            "r",
            "结果页",
            "vlm_judge",
            predicate=CORE_PREDICATE_CATALOG.create_spec(
                "semantic.entity_matches", "结果页"
            ),
            provenance="state",
        )
        assert _is_self_observable(state_judge) is False


# ----------------------------------------------------------------------
# L3: sealing gates (observation-driven, S2)
# ----------------------------------------------------------------------


class TestObservationDrivenSealing:
    def test_stage_seals_when_all_done_criteria_observed(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="早上6点到12点",
            step=3, screen="filter", epoch=3,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="filter", step=3,
        )
        assert [s["stage_id"] for s in seals] == ["S1"]
        assert seals[0]["provenance_by_criterion"] == {
            "time_filter_confirmed": "confirmed"
        }

    def test_stage_does_not_seal_without_observation(self) -> None:
        contract = _residual_contract()
        ledger, seals = seal_satisfied_stages(
            [], contract=contract, contract_id=contract.task_hash,
            screen_id="results", step=5,
        )
        assert seals == []

    def test_not_visible_read_does_not_seal(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", status="not_visible",
            step=3, screen="results", epoch=3,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="results", step=3,
        )
        assert seals == []

    def test_later_stage_seals_after_its_criteria_observed(self) -> None:
        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="早上6点到12点",
            step=3, screen="filter", epoch=3,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="filter", step=3,
        )
        assert [s["stage_id"] for s in seals] == ["S1"]
        ledger = _observe(
            ledger, contract, "flight_results", value="航班列表",
            step=4, screen="results", epoch=4,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="results", step=4,
        )
        assert [s["stage_id"] for s in seals] == ["S2"]

    def test_contradicted_read_revokes_seal(self) -> None:
        """P0 #13a: a positive model counter-observation revokes the seal;
        absence (not_visible) never does."""
        from phone_agent.graph.goal_evidence import revoke_seals_on_contradiction

        contract = _residual_contract()
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="早上6点到12点",
            step=3, screen="filter", epoch=3,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="filter", step=3,
        )
        assert seals
        ledger = revoke_seals_on_contradiction(
            ledger,
            contract=contract,
            contract_id=contract.task_hash,
            contradicted_criteria={"time_filter_confirmed"},
            screen_id="results",
            step=4,
        )
        assert seal_records_for_contract(
            ledger, contract=contract, contract_id=contract.task_hash
        ) == []
        # Absence never revokes.
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="早上6点到12点",
            step=3, screen="filter", epoch=3,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="filter", step=3,
        )
        assert seals
        ledger = revoke_seals_on_contradiction(
            ledger,
            contract=contract,
            contract_id=contract.task_hash,
            contradicted_criteria=set(),
        )
        assert seal_records_for_contract(
            ledger, contract=contract, contract_id=contract.task_hash
        )


class TestDuplicateDoneCriteria:
    def test_identical_done_sets_are_rejected(self) -> None:
        plan = (
            TaskStage("S1", "a", ("c1",), "", 0),
            TaskStage("S2", "b", ("c1",), "", 1),  # same set as S1
        )
        errors = task_plan_validation_errors(
            plan,
            criterion_names=["c1"],
            criteria={"c1": SuccessCriterion("c1", "d", "vlm_judge")},
        )
        assert any(
            "duplicate_done_criteria_of_stage" in error for error in errors
        )

    def test_llm_compiler_rejects_duplicate_done_sets(self) -> None:
        @dataclass
        class Response:
            action: str

        class Client:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, messages, **kwargs):
                self.calls += 1
                return Response(
                    json.dumps(
                        {
                            "objective": "t",
                            "success_criteria": [
                                {"name": "c1", "description": "d1", "verification": "vlm_judge", "required": True}
                            ],
                            "constraints": [],
                            "non_goals": [],
                            "target_app_hint": None,
                            "ordinal": None,
                            "task_plan": [
                                {"objective": "a", "done_criteria": ["c1"], "fallback": ""},
                                {"objective": "b", "done_criteria": ["c1"], "fallback": ""},
                                {"objective": "c", "done_criteria": ["c1"], "fallback": ""},
                            ],
                        },
                        ensure_ascii=False,
                    )
                )

        client = Client()
        contract = LLMGoalCompiler(client, lang="cn", retry_limit=0).compile(task="x")
        assert contract.compile_status == "failed"


class TestSealKeyCollision:
    def test_shared_key_resolves_to_smallest_index(self) -> None:
        """pi-23's key-collision artifact: two stages with identical done
        criteria share a semantic key; the seal must belong to the FIRST."""
        contract = _residual_contract()
        collapsed = GoalContract(
            task_hash="c1",
            redacted_objective="t",
            objective_length=1,
            success_criteria=_residual_contract().success_criteria,
            task_plan=(
                TaskStage("S1", "应用筛选", ("time_filter_confirmed",), "", 0),
                TaskStage("S2", "重复筛选", ("time_filter_confirmed",), "", 1),
            ),
            compile_status="compiled",
        )
        criteria = {c.name: c for c in collapsed.success_criteria}
        key = stage_semantic_key(collapsed.task_plan[0], criteria)
        ledger = _observe(
            [], collapsed, "time_filter_confirmed", value="早上6点到12点",
            step=6, screen="filter-panel", epoch=6,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=collapsed, contract_id="c1", screen_id="filter-panel", step=6
        )
        # Only ONE seal record for the shared key, resolved to S1 (first-wins).
        assert len(seals) == 1
        records = seal_records_for_contract(
            ledger, contract=collapsed, contract_id="c1"
        )
        assert [r["stage_id"] for r in records] == ["S1"]


# ----------------------------------------------------------------------
# L4: plan wiring
# ----------------------------------------------------------------------


class TestGapStatus:
    def test_gap_status_current_stage_and_sealed_rows(self) -> None:
        contract = _residual_contract()
        # No model screen-read yet: the current stage stays S1 and the
        # confirmed criterion is pending.
        gap = criterion_gap_status(
            contract=contract,
            ledger=[],
            contract_id=contract.task_hash,
            screen_id="results",
            observation_epoch=9,
        )
        assert gap is not None
        assert gap["current_stage_id"] == "S1"
        item = next(i for i in gap["items"] if i["name"] == "time_filter_confirmed")
        assert item["status"] == "pending"
        assert item["provenance"] == "confirmed"
        assert item["control_hint"] == "打开筛选面板读取时段值"
        # An observed model screen-read flips it to satisfied; the stage seals
        # and the gap list advances to S2 with the sealed one-liner.
        ledger = _observe(
            [], contract, "time_filter_confirmed", value="早上6点到12点",
            step=9, screen="filter-panel", epoch=9,
        )
        ledger, seals = seal_satisfied_stages(
            ledger, contract=contract, contract_id=contract.task_hash,
            screen_id="filter-panel", step=9,
        )
        assert [s["stage_id"] for s in seals] == ["S1"]
        ledger = _observe(
            ledger, contract, "flight_results", value="航班列表",
            step=10, screen="results", epoch=10,
        )
        gap2 = criterion_gap_status(
            contract=contract,
            ledger=ledger,
            contract_id=contract.task_hash,
            screen_id="results",
            observation_epoch=10,
        )
        assert gap2["current_stage_id"] is None  # every stage fold-satisfied
        assert any(
            r["name"] == "time_filter_confirmed"
            and r["stage_id"] == "S1"
            for r in gap2["sealed"]
        )

    def test_gap_status_none_without_plan(self) -> None:
        assert (
            criterion_gap_status(
                contract=HeuristicGoalCompiler().compile(task="打开设置"),
                ledger=[],
                contract_id="c",
                screen_id="s",
                observation_epoch=1,
            )
            is None
        )


class TestGapListRender:
    def _state(self, gap: dict, lang: str = "cn") -> dict:
        return {
            "task": "查机票",
            "lang": lang,
            "criterion_gap_list": gap,
            "goal_contract": _residual_contract().to_state_payload(runtime_reference="r1"),
            "task_plan_status": None,
            "goal_agenda": [],
            "action_parsed": None,
            "action_result": None,
            "failure_memory": [],
            "gui_memory": {},
            "grounding_observation": None,
            "current_app": "ctrip",
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

    def _gap(self) -> dict:
        return {
            "current_stage_id": "S1",
            "current_objective": "应用筛选",
            "items": [
                {
                    "name": "time_filter_confirmed",
                    "description": "筛选面板显示‘早上6点到12点’时段",
                    "status": "pending",
                    "provenance": "confirmed",
                    "control_hint": "打开筛选面板读取时段值",
                },
                {
                    "name": "route_search",
                    "description": "路线 从北京到上海",
                    "status": "pending",
                    "provenance": "state",
                    "control_hint": None,
                },
            ],
            "sealed": [
                {"name": "launch_ok", "stage_id": "S0", "description": "app 前台"}
            ],
        }

    def test_render_cn(self) -> None:
        block, _ = build_plan_context_block(self._state(self._gap()), lang="cn")
        assert "判据缺口清单" in block
        assert "⏳ time_filter_confirmed [需确认]" in block
        assert "必须读取控件实际值，从结果列表推断不算数" in block
        assert "⏳ route_search [需观察]" in block
        assert "✅ launch_ok（S0 已确认）" in block

    def test_render_en(self) -> None:
        block, _ = build_plan_context_block(
            self._state(self._gap(), lang="en"), lang="en"
        )
        assert "criterion_gap_list" in block
        assert "⏳ time_filter_confirmed [confirm]" in block
        assert "inferring from the result list does not count" in block
        assert "✅ launch_ok (confirmed at S0)" in block

    def test_gap_list_text_is_redacted(self) -> None:
        gap = self._gap()
        gap["items"][0]["control_hint"] = "联系 13800138000"
        block, _ = build_plan_context_block(self._state(gap), lang="cn")
        assert "13800138000" not in block

    def test_prompt_side_semantics_cn_en_paired(self) -> None:
        for rules in (CONTEXT_USAGE_RULES_ZH, CONTEXT_USAGE_RULES_EN):
            assert "criterion_gap_list" in rules
            assert "⏳" in rules
            assert "✅" in rules
        assert "[需确认]" in CONTEXT_USAGE_RULES_ZH
        assert "result list does not count" in CONTEXT_USAGE_RULES_EN


class TestReflectWritesGapList:
    def test_reflect_emits_gap_list(self, base_state, fake_device) -> None:
        contract = _residual_contract()
        base_state["goal_contract"] = contract
        base_state["task"] = "查机票"
        base_state["expected_outcome"] = {
            "kind": "generic",
            "must_observe": [],
            "must_not_observe": [],
            "target_mark_id": None,
            "target_text_hint": None,
            "timeout_hint": None,
            "dynamic_regions": [],
        }
        base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [1, 2]}
        base_state["action_result"] = {"success": True, "should_finish": False, "message": "ok"}

        class Response:
            action = (
                '{"verdict":"succeeded","failure_cause":"none",'
                '"suggested_strategy":"continue","message":"ok"}'
            )

        class Model:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, messages, **kwargs):
                self.calls += 1
                return Response()

        model = Model()
        result = reflect_node(
            base_state,
            {
                "configurable": {
                    "model_client": model,
                    "device_factory": fake_device,
                    "verbose": False,
                    "after_screen_marks": [
                        {
                            "mark_id": "ax_home",
                            "bbox": [50, 200, 900, 300],
                            "role": "TextView",
                            "source": "accessibility_tree",
                            "text_summary": "首页",
                        }
                    ],
                    "grounding_provider_name": "off",
                }
            },
        )
        gap = result.get("criterion_gap_list")
        assert isinstance(gap, dict)
        assert gap["current_stage_id"] == "S1"
        item = next(
            i for i in gap["items"] if i["name"] == "time_filter_confirmed"
        )
        assert item["status"] == "pending"
        assert item["provenance"] == "confirmed"


# ----------------------------------------------------------------------
# Mandatory end-to-end regression (160431 携程 residual scenario)
# ----------------------------------------------------------------------

CTRIP_TASK = "在携程查明天早上6点到12点从北京到上海最便宜的航班"


def _e2e_contract() -> GoalContract:
    """Every explicit parameter constraint carries its own criterion."""
    return GoalContract(
        task_hash=compute_task_binding(CTRIP_TASK),
        redacted_objective=CTRIP_TASK,
        objective_length=len(CTRIP_TASK),
        success_criteria=[
            SuccessCriterion(
                "time_filter_confirmed",
                "筛选面板显示‘早上6点到12点’时段",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "早上6点到12点"
                ),
                provenance="confirmed",
                control_hint="打开筛选面板读取时段值",
                required=True,
            ),
            SuccessCriterion(
                "route_search",
                "路线 从北京到上海",
                "vlm_judge",
                required=True,
            ),
            SuccessCriterion(
                "cheapest_identified",
                "结果页价格从低到高（最便宜）",
                "vlm_judge",
                required=True,
            ),
            SuccessCriterion(
                "flight_results",
                "航班列表卡片",
                "vlm_judge",
                required=True,
            ),
        ],
        target_app_hint="携程",
        entities_sha=list(
            TaskRequirementExtractor().extract(CTRIP_TASK).target_entity_hashes
        ),
        task_plan=(
            TaskStage("S1", "应用筛选", ("time_filter_confirmed", "route_search"), "", 0),
            TaskStage("S2", "结果页", ("cheapest_identified", "flight_results"), "", 1),
        ),
        compile_status="compiled",
        compile_source="external",
    )


@dataclass
class _FakeModelResponse:
    thinking: str
    action: str


class _FakeModelClient:
    def __init__(self, response: _FakeModelResponse) -> None:
        self.response = response
        self.messages: list[dict] | None = None

    def request(self, messages, **kwargs):
        self.messages = messages
        return self.response


def _bind_e2e(base_state) -> tuple[str, RuntimeGoalContext]:
    requirements = TaskRequirementExtractor().extract(CTRIP_TASK)
    runtime_goal = RuntimeGoalContext()
    update = goal_node(
        {
            "task": CTRIP_TASK,
            "lang": "cn",
            "step_count": 0,
            "goal_contract_status": "pending",
        },
        {
            "configurable": {
                "runtime_goal_context": runtime_goal,
                "task_goal_contract_override": _e2e_contract(),
                "task_requirement_set_override": requirements,
            }
        },
    )
    assert update["goal_contract_status"] == "user_override", update.get(
        "contract_adequacy_reasons"
    )
    base_state.update(update)
    return base_state["goal_contract"]["runtime_reference"], runtime_goal


def _seed_e2e(base_state) -> None:
    base_state["task"] = CTRIP_TASK
    base_state["goal_contract_status"] = "compiled"
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": [
            "time_filter_confirmed",
            "route_search",
            "cheapest_identified",
            "flight_results",
        ],
    }
    base_state["action_result"] = {
        "success": True,
        "should_finish": False,
        "message": "done",
    }
    base_state["pending_finish"] = True
    base_state["expected_outcome"] = None


def _e2e_config(fake_device, model, marks, extra=None) -> dict:
    config = {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "verbose": False,
            "after_screen_marks": marks,
            "grounding_provider_name": "off",
        }
    }
    if extra:
        config["configurable"].update(extra)
    return config


_RESIDUAL_LEDGER_TEXT = "筛选 早上6点到12点 最便宜"


def _residual_ledger(contract: GoalContract, reference: str) -> list[dict]:
    """The 160431 leftover: the previous run left the filter + sort applied;
    a trusted digest and a matched evaluation entry recorded it on an EARLIER
    screen (epoch 2) — exactly what used to sail through the ever-matched
    latch and the judge's result-list derivation."""
    keys = _semantic_keys(contract)
    ledger = append_screen_text_digest(
        [],
        contract_id=reference,
        screen_id="results-old",
        observation_epoch=2,
        marks=[
            {
                "mark_id": "ax_old",
                "source": "accessibility_tree",
                "text_summary": _RESIDUAL_LEDGER_TEXT,
            }
        ],
        target_app_entered=True,
    )
    for name, predicate_id in (
        ("time_filter_confirmed", "semantic.entity_matches"),
        ("route_search", "semantic.entity_matches"),
        ("cheapest_identified", "semantic.entity_matches"),
    ):
        ledger = append_evaluation_entries(
            ledger,
            evaluation={
                "evidence": {
                    "per_criterion": {
                        name: {
                            "status": "matched",
                            "reason": "existential_match",
                            "source": "accessibility",
                        }
                    }
                }
            },
            contract_id=reference,
            screen_id="results-old",
            observation_epoch=2,
            predicate_ids={name: predicate_id},
            target_app_entered=True,
            semantic_keys=keys,
        )
    return ledger


_RESULTS_MARKS = [
    {
        "mark_id": "ax_flight1",
        "bbox": [50, 100, 900, 200],
        "role": "TextView",
        "text_summary": "航班 06:05 起飞",
    },
    {
        "mark_id": "ax_flight2",
        "bbox": [50, 220, 900, 320],
        "role": "TextView",
        "text_summary": "航班 09:30 起飞",
    },
]


class _FilterPanelStructureProvider:
    """Injects an accessibility structure whose control text carries the
    interval — the confirmation-read observation (E4)."""

    name = "ax-filter-panel"
    version = "test_v1"

    def __init__(self, text: str) -> None:
        self.text = text

    def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
        from phone_agent.grounding.provider import MarkProviderResult

        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_structures=[
                {
                    "structure_kind": "accessibility",
                    "source_provider": self.name,
                    "nodes": {
                        "n_panel": {
                            "node_id": "n_panel",
                            "path": "/panel",
                            "role": "TextView",
                            "text_summary": self.text,
                            "visible": True,
                        }
                    },
                }
            ],
        )


def test_e2e_residual_filter_blocks_finish_until_control_read(
    base_state, fake_device
) -> None:
    """The 160431 regression, end to end (S2/S3 semantics):

    1. Residual filter + sort from the previous run, contract with a
       confirmed time-window criterion, and a judge that asserts success
       WITHOUT naming where it read each criterion (no evidence_step
       reference) → finish must be BLOCKED (form-only reference validation)
       and the rejection feedback must guide the agent back to the evidence.
    2. After the agent actually reads the value on the control (the judge
       reports each criterion with a valid evidence_step reference), the
       same finish claim passes.
    """
    contract = _e2e_contract()
    reference, runtime_goal = _bind_e2e(base_state)
    _seed_e2e(base_state)
    base_state["goal_evidence_ledger"] = _residual_ledger(contract, reference)

    # The judge does what it did in the incident: it asserts every criterion
    # satisfied without naming the step/screen where it read them.
    judge_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "time_filter_confirmed",
                            "status": "satisfied",
                            "observed_value": "航班 06:05 起飞",
                        },
                        {"criterion": "route_search", "status": "satisfied"},
                        {"criterion": "cheapest_identified", "status": "satisfied"},
                        {"criterion": "flight_results", "status": "satisfied"},
                    ],
                    "message": "done",
                }
            ),
        )
    )
    config = _e2e_config(fake_device, judge_model, _RESULTS_MARKS)
    config["configurable"]["runtime_goal_context"] = runtime_goal

    rejected = acceptance_node(base_state, config)

    assert rejected["finished"] is False
    assert rejected["failure_cause"] == "goal_not_satisfied"
    per_criterion = rejected["finish_validation_evidence"]["evidence"][
        "per_criterion"
    ]
    assert per_criterion["time_filter_confirmed"]["status"] == "unknown"
    # The judge's reference-less assertion is rejected (form-only check),
    # and the residual criterion has no model screen-read either.
    assert per_criterion["time_filter_confirmed"]["reason"] == (
        "judge_reference_missing_or_out_of_range"
    )
    feedback = rejected["acceptance_rejection_feedback"]
    assert isinstance(feedback, dict) and isinstance(feedback.get("missing"), list)
    time_hint = next(
        item
        for item in feedback["missing"]
        if item["criterion"] == "time_filter_confirmed"
    )
    assert time_hint["stage_id"] == "S1"
    assert "筛选" in time_hint["hint"] or "面板" in time_hint["hint"]
    assert "不算数" in time_hint["hint"]

    # --- round 2: the agent opens the filter panel and reads the control; the
    # judge reports every criterion satisfied WITH a valid evidence_step ---
    base_state.update(rejected)
    _seed_e2e(base_state)
    base_state["goal_evidence_ledger"] = rejected["goal_evidence_ledger"]
    judge_model2 = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "time_filter_confirmed",
                            "status": "satisfied",
                            "observed_value": "筛选面板显示早上6点到12点",
                            "evidence_step": "final_screen",
                        },
                        {
                            "criterion": "route_search",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        },
                        {
                            "criterion": "cheapest_identified",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        },
                        {
                            "criterion": "flight_results",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        },
                    ],
                    "message": "done",
                }
            ),
        )
    )
    config2 = _e2e_config(
        fake_device,
        judge_model2,
        [
            {
                "mark_id": "ax_panel",
                "bbox": [50, 100, 900, 300],
                "role": "TextView",
                "text_summary": "时段选择",
            }
        ],
    )
    config2["configurable"]["runtime_goal_context"] = runtime_goal
    passed = acceptance_node(base_state, config2)

    assert passed["finished"] is True
    assert passed["finish_validation_status"] == "success"
    per_criterion2 = passed["finish_validation_evidence"]["evidence"][
        "per_criterion"
    ]
    assert per_criterion2["time_filter_confirmed"]["status"] == "satisfied"
    assert per_criterion2["time_filter_confirmed"]["reason"] == "judge_verdict"

    # Negative control: without the control read, the SAME final screen can
    # never pass, even with the residual ledger and a judge claiming success
    # without references.
    base_state["goal_evidence_ledger"] = _residual_ledger(contract, reference)
    _seed_e2e(base_state)
    config3 = _e2e_config(fake_device, judge_model, _RESULTS_MARKS)
    config3["configurable"]["runtime_goal_context"] = runtime_goal
    rejected3 = acceptance_node(base_state, config3)
    assert rejected3["finished"] is False
