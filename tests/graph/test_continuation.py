"""F2 earned-continuation: pure credential branches + window budget semantics."""

from dataclasses import dataclass, replace

import pytest

from phone_agent.config.policy import (
    CONTINUATION_GRANT_STEPS,
    CONTINUATION_MAX_GRANTS,
)
from phone_agent.graph.context import (
    build_budget_section,
    continuation_credential,
)
from phone_agent.graph.goal import CriterionSpec
from phone_agent.graph.goal_compiler import HeuristicGoalCompiler
from phone_agent.graph.nodes.acceptance import acceptance_node


@dataclass
class _FakeModelResponse:
    thinking: str
    action: str


class _FakeModelClient:
    def __init__(self, response: _FakeModelResponse) -> None:
        self.response = response

    def request(self, messages, **kwargs):
        return self.response


def _contract(runtime_reference: str = "r1", names=("c1", "c2")) -> dict:
    return {
        "runtime_reference": runtime_reference,
        "success_criteria": [{"name": name} for name in names],
    }


def _contract_with_verification(verifications: dict[str, str]) -> dict:
    """Contract whose criteria carry explicit verification metadata (H5)."""
    return {
        "runtime_reference": "r1",
        "success_criteria": [
            {"name": name, "verification": kind}
            for name, kind in verifications.items()
        ],
    }


def _entry(epoch, criterion, status, *, target_app_entered=None) -> dict:
    return {
        "contract_id": "r1",
        "criterion_id": criterion,
        "status": status,
        "screen_id": f"s{epoch}",
        "observation_epoch": epoch,
        "target_app_entered": target_app_entered,
    }


def _state(**overrides) -> dict:
    state = {
        "goal_contract": _contract(),
        "goal_evidence_ledger": [],
        "continuation_last_latch_count": 0,
        "gui_memory": {"task_progress": {}},
        "finish_validation_evidence": None,
        "max_steps": 10,
        "absolute_max_steps": 30,
        "continuation_count": 0,
        "step_count": 10,
        "locate_count": 0,
    }
    state.update(overrides)
    return state


# ----------------------------------------------------------------------
# Branch 1: criterion movement (net up over the window)
# ----------------------------------------------------------------------


def test_credential_grants_on_criterion_rank_up() -> None:
    ledger = [
        _entry(1, "c1", "unknown"),
        _entry(2, "c1", "unknown"),
        _entry(3, "c1", "unknown"),
        _entry(4, "c1", "matched"),
    ]
    credential = continuation_credential(_state(goal_evidence_ledger=ledger))

    assert credential.granted is True
    assert "criterion_movement" in credential.branches


def test_credential_does_not_grant_on_aba_oscillation() -> None:
    """A-B-A-B status oscillation (matched↔missing) is not net progress."""
    ledger = [
        _entry(1, "c1", "matched"),
        _entry(2, "c1", "missing"),
        _entry(3, "c1", "matched"),
        _entry(4, "c1", "missing"),
    ]
    credential = continuation_credential(_state(goal_evidence_ledger=ledger))

    assert credential.granted is False
    assert credential.reason == "no_progress_evidence"


def test_credential_does_not_grant_on_feed_refresh_fake_exploration() -> None:
    """Novel screens each refresh, but no criterion ever moves: no credential."""
    ledger = [
        _entry(1, "c1", "unknown"),
        _entry(2, "c1", "unknown"),
        _entry(3, "c1", "missing"),
        _entry(4, "c1", "unknown"),
    ]
    credential = continuation_credential(
        _state(
            goal_evidence_ledger=ledger,
            gui_memory={"task_progress": {"novelty_streak": 0}},
        )
    )

    assert credential.granted is False
    assert credential.reason == "no_progress_evidence"


def test_credential_criterion_window_is_bounded_to_last_steps() -> None:
    """Movement older than the window does not count."""
    ledger = [_entry(epoch, "c1", "matched") for epoch in range(1, 20)] + [
        _entry(21, "c1", "missing")
    ]
    credential = continuation_credential(_state(goal_evidence_ledger=ledger))

    assert credential.granted is False


# ----------------------------------------------------------------------
# Branch 2: new latch (Goal facts, exempt from novelty negation)
# ----------------------------------------------------------------------


def test_credential_grants_on_new_latch_even_with_high_novelty_streak() -> None:
    ledger = [_entry(1, "c1", "matched", target_app_entered=True)]
    credential = continuation_credential(
        _state(
            goal_evidence_ledger=ledger,
            continuation_last_latch_count=0,
            gui_memory={"task_progress": {"novelty_streak": 20}},
        )
    )

    assert credential.granted is True
    assert "new_latch" in credential.branches


def test_credential_no_grant_when_latch_count_unchanged() -> None:
    ledger = [_entry(1, "c1", "matched", target_app_entered=True)]
    credential = continuation_credential(
        _state(
            goal_evidence_ledger=ledger,
            continuation_last_latch_count=1,
            gui_memory={"task_progress": {"novelty_streak": 20}},
        )
    )

    assert credential.granted is False
    assert credential.reason == "novelty_exhausted"


def test_credential_contradiction_unlatches_before_boundary() -> None:
    """A contradiction after the latch unlocks it, so no new latch at the edge."""
    ledger = [
        _entry(1, "c1", "matched", target_app_entered=True),
        _entry(2, "c1", "contradicted", target_app_entered=True),
    ]
    credential = continuation_credential(
        _state(goal_evidence_ledger=ledger, continuation_last_latch_count=0)
    )

    assert credential.granted is False


# ----------------------------------------------------------------------
# Branch 3: judge near-miss
# ----------------------------------------------------------------------


def test_credential_grants_on_judge_near_miss() -> None:
    credential = continuation_credential(
        _state(finish_validation_evidence={"matched": ["c1"], "matched_terminal_evidence": ["c1"]})
    )

    assert credential.granted is True
    assert "judge_near_miss" in credential.branches


def test_credential_rejects_empty_judge_evidence() -> None:
    credential = continuation_credential(
        _state(finish_validation_evidence={"matched": [], "matched_terminal_evidence": []})
    )

    assert credential.granted is False


# ----------------------------------------------------------------------
# H5: branches 2/3 count only judge-type criteria (auto standards excluded)
# ----------------------------------------------------------------------


def test_credential_auto_only_latch_does_not_grant() -> None:
    """H5: an ever-matched auto standard (app_or_activity_match) is 恒真 and
    must not earn a continuation latch on its own."""
    ledger = [_entry(1, "c1", "matched", target_app_entered=True)]
    credential = continuation_credential(
        _state(
            goal_contract=_contract_with_verification({"c1": "app_or_activity_match"}),
            goal_evidence_ledger=ledger,
            continuation_last_latch_count=0,
            gui_memory={"task_progress": {"novelty_streak": 0}},
        )
    )

    assert credential.granted is False
    assert "new_latch" not in credential.branches
    assert credential.reason == "no_progress_evidence"


def test_credential_judge_latch_grants() -> None:
    """H5: an ever-matched judge criterion (vlm_judge) earns the latch."""
    ledger = [_entry(1, "c1", "matched", target_app_entered=True)]
    credential = continuation_credential(
        _state(
            goal_contract=_contract_with_verification({"c1": "vlm_judge"}),
            goal_evidence_ledger=ledger,
            continuation_last_latch_count=0,
            gui_memory={"task_progress": {"novelty_streak": 0}},
        )
    )

    assert credential.granted is True
    assert "new_latch" in credential.branches


def test_credential_mixed_contract_counts_only_judge_latches() -> None:
    """H5: with an auto + judge contract, only the judge latch counts toward
    the new_latch comparison."""
    contract = _contract_with_verification(
        {"c1": "vlm_judge", "c2": "app_or_activity_match"}
    )
    # Only the auto standard latched: no new latch.
    auto_only = continuation_credential(
        _state(
            goal_contract=contract,
            goal_evidence_ledger=[
                _entry(1, "c2", "matched", target_app_entered=True)
            ],
            continuation_last_latch_count=0,
        )
    )
    assert auto_only.granted is False

    # Both latched: the judge criterion supplies the new latch.
    both = continuation_credential(
        _state(
            goal_contract=contract,
            goal_evidence_ledger=[
                _entry(1, "c1", "matched", target_app_entered=True),
                _entry(1, "c2", "matched", target_app_entered=True),
            ],
            continuation_last_latch_count=0,
        )
    )
    assert both.granted is True
    assert "new_latch" in both.branches


def test_credential_judge_near_miss_excludes_auto_evidence() -> None:
    """H5: near-miss evidence naming only auto-standard criteria is not a
    judge near-miss."""
    credential = continuation_credential(
        _state(
            goal_contract=_contract_with_verification({"c1": "app_or_activity_match"}),
            finish_validation_evidence={
                "matched": ["c1"],
                "matched_terminal_evidence": ["c1"],
            },
        )
    )

    assert credential.granted is False
    assert "judge_near_miss" not in credential.branches


def test_credential_judge_near_miss_grants_on_judge_evidence() -> None:
    """H5: near-miss evidence naming a judge-type criterion earns the window."""
    credential = continuation_credential(
        _state(
            goal_contract=_contract_with_verification({"c1": "vlm_judge"}),
            finish_validation_evidence={
                "matched": ["c1"],
                "matched_terminal_evidence": ["c1"],
            },
        )
    )

    assert credential.granted is True
    assert "judge_near_miss" in credential.branches


def test_credential_judge_near_miss_mixed_evidence_needs_one_judge() -> None:
    """H5: auto evidence alone never grants; adding one judge match does."""
    contract = _contract_with_verification(
        {"c1": "vlm_judge", "c2": "app_or_activity_match"}
    )
    auto_only = continuation_credential(
        _state(
            goal_contract=contract,
            finish_validation_evidence={"matched": ["c2"], "matched_terminal_evidence": ["c2"]},
        )
    )
    assert auto_only.granted is False

    mixed = continuation_credential(
        _state(
            goal_contract=contract,
            finish_validation_evidence={
                "matched": ["c1", "c2"],
                "matched_terminal_evidence": ["c1", "c2"],
            },
        )
    )
    assert mixed.granted is True
    assert "judge_near_miss" in mixed.branches


# ----------------------------------------------------------------------
# Negation
# ----------------------------------------------------------------------


def test_credential_denied_on_novelty_exhaustion_without_branches() -> None:
    credential = continuation_credential(
        _state(gui_memory={"task_progress": {"novelty_streak": 4}})
    )

    assert credential.granted is False
    assert credential.reason == "novelty_exhausted"


def test_credential_denied_without_any_evidence() -> None:
    credential = continuation_credential(_state())

    assert credential.granted is False
    assert credential.reason == "no_progress_evidence"


# ----------------------------------------------------------------------
# Budget visibility section (F2.2)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "budget_state", "present", "absent"),
    [
        (
            "cn",
            {"max_steps": 20, "step_count": 5, "continuation_count": 1},
            ("剩余 15/20 步", "已续命 1/2 次", "预算耗尽≠失败"),
            ("将尽",),
        ),
        (
            "en",
            {"max_steps": 20, "step_count": 15, "continuation_count": 0},
            ("5/20 steps left", "Budget exhaustion is NOT failure", "nearly exhausted"),
            (),
        ),
    ],
)
def test_budget_section_reports_remaining_steps_and_grants(
    lang: str,
    budget_state: dict,
    present: tuple[str, ...],
    absent: tuple[str, ...],
) -> None:
    block = build_budget_section(budget_state, lang=lang)

    for marker in present:
        assert marker in block
    for marker in absent:
        assert marker not in block


def test_budget_section_empty_without_max_steps() -> None:
    assert build_budget_section({"max_steps": 0, "step_count": 0}, lang="cn") == ""


def test_budget_section_omits_locate_countdown() -> None:
    """Effect-guards: the locate budget is a runaway fuse, not a scarcity
    budget — the "locate 剩余 x/3" countdown is gone from both languages (the
    scarcity hint actively induced the model to abandon mid-task)."""
    block = build_budget_section(
        {"max_steps": 20, "step_count": 5, "continuation_count": 1, "locate_count": 1},
        lang="cn",
    )

    assert "locate" not in block

    block_en = build_budget_section(
        {"max_steps": 20, "step_count": 5, "continuation_count": 1, "locate_count": 3},
        lang="en",
    )

    assert "locate" not in block_en


def test_budget_section_locate_line_absent_without_max_steps() -> None:
    assert build_budget_section({"max_steps": 0, "locate_count": 0}, lang="cn") == ""


# ----------------------------------------------------------------------
# Acceptance-node window semantics (grant written in the node, edges pure)
# ----------------------------------------------------------------------


def _budget_state(**overrides) -> dict:
    contract = HeuristicGoalCompiler().compile(task="测试任务")
    extra = CriterionSpec(
        name="target_app_visible",
        description="目标应用在前台",
        verification="app_or_activity_match",
        required=True,
    )
    contract = replace(
        contract,
        success_criteria=[contract.success_criteria[0], extra],
        target_app_hint="com.example.notrunning",
    )
    state = {
        "task": "测试任务",
        "goal_contract": contract,
        "goal_contract_status": "compiled",
        "lang": "cn",
        "step_count": 20,
        "max_steps": 20,
        "finished": False,
        "error": None,
        "pending_finish": False,
        "budget_acceptance_done": False,
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        "action_result": {"success": True, "should_finish": False, "message": "ok"},
        "observation_retry_count": 0,
        "acceptance_round_count": 0,
        "context_mode": "inject",
        "screen_belief": {},
        "goal_evidence_ledger": [],
        "expected_outcome": None,
        "failure_memory": [],
        "summarized_history": "",
        "gui_memory": {},
        "absolute_max_steps": 60,
        "continuation_count": 0,
        "continuation_last_latch_count": 0,
        "locate_count": 0,
    }
    state.update(overrides)
    return state


def _acceptance_config(fake_device) -> dict:
    return {
        "configurable": {
            "model_client": _FakeModelClient(
                _FakeModelResponse(
                    "ok",
                    '{"completed":false,"message":"还差目标应用",'
                    '"named_evidence":[{"criterion":"task_completed",'
                    '"screen_reference":"mark_id=done","observed_value":"测试任务"}]}',
                )
            ),
            "device_factory": fake_device,
            "verbose": False,
            "after_screen_marks": [
                {
                    "mark_id": "tab",
                    "bbox": [0, 0, 1000, 100],
                    "role": "TextView",
                    "text_summary": "首页",
                }
            ],
            "grounding_provider_name": "off",
        }
    }


def test_window_rejection_with_judge_near_miss_grants_continuation(
    base_state, fake_device
) -> None:
    """2.1: a rejected budget-forced acceptance with judge near-miss evidence
    earns a new window: max_steps += grant, continuation_count++, and
    budget_acceptance_done resets so a new forced acceptance can fire."""
    result = acceptance_node(_budget_state(), _acceptance_config(fake_device))

    assert result["finished"] is False
    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["max_steps"] == 20 + CONTINUATION_GRANT_STEPS
    assert result["continuation_count"] == 1
    assert result["budget_acceptance_done"] is False
    assert result["finish_source"] is None
    assert result["continuation_last_latch_count"] == 0
    # Edges are pure: with the grant the run keeps going.
    from phone_agent.graph.edges import after_acceptance

    assert after_acceptance({**base_state, **result}) == "replan"


def test_window_rejection_without_credential_ends(base_state, fake_device) -> None:
    """No credential (no branches, no novelty) → no grant → end at max_steps."""
    from phone_agent.graph.edges import after_acceptance

    model = _FakeModelClient(
        _FakeModelResponse("ok", '{"completed":false,"message":"任务未完成"}')
    )
    state = _budget_state()
    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "tab",
                        "bbox": [0, 0, 1000, 100],
                        "role": "TextView",
                        "text_summary": "首页",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["budget_acceptance_done"] is True
    merged = {**state, **result}
    assert merged["max_steps"] == 20
    assert merged["continuation_count"] == 0
    assert after_acceptance(merged) == "end"


def test_window_rejection_capped_by_max_grants(base_state, fake_device) -> None:
    """At CONTINUATION_MAX_GRANTS the credential is refused even with evidence."""
    state = _budget_state(continuation_count=CONTINUATION_MAX_GRANTS)
    result = acceptance_node(state, _acceptance_config(fake_device))

    assert result["failure_cause"] == "goal_not_satisfied"
    merged = {**state, **result}
    assert merged["continuation_count"] == CONTINUATION_MAX_GRANTS
    assert result["budget_acceptance_done"] is True


def test_absolute_budget_exhaustion_forces_acceptance_then_end(
    base_state, fake_device
) -> None:
    """Absolute ceiling: finish_source=absolute_budget_exhausted, no grant."""
    from phone_agent.graph.edges import after_acceptance

    state = _budget_state(step_count=60, max_steps=60, absolute_max_steps=60)
    result = acceptance_node(state, _acceptance_config(fake_device))

    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["finish_source"] == "absolute_budget_exhausted"
    merged = {**state, **result}
    assert merged["max_steps"] == 60
    assert merged["continuation_count"] == 0
    assert result["budget_acceptance_done"] is True
    assert after_acceptance(merged) == "end"


def test_absolute_ceiling_blocks_grant_even_with_credential(
    base_state, fake_device
) -> None:
    """Grant math must never push max_steps past the absolute ceiling."""
    state = _budget_state(
        step_count=55, max_steps=55, absolute_max_steps=60
    )
    result = acceptance_node(state, _acceptance_config(fake_device))

    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["finish_source"] == "budget_forced"
    merged = {**state, **result}
    assert merged["max_steps"] == 55
    assert merged["continuation_count"] == 0
