"""End-to-end finish gate: real compilation, real terminal observation.

The prior suite asserted what contracts *looked like*, never whether one could
be satisfied — and its single success-path test hand-fed the gate the exact
internal values a real model could not know (`observed_value: "bilibili"`,
`source: "device"`). Two task families were therefore permanently unfinishable
with every test green.

These tests go the other way: compile through the production chain (no injected
contract), build a terminal observation the way a device would report it, and
assert the gate opens. Each case also asserts the negative, so "always accept"
cannot pass.
"""

import pytest

from phone_agent.graph.goal_compiler import HeuristicGoalCompiler
from phone_agent.graph.goal_evaluator import _is_self_observable, evaluate_finish_claim
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
)


def _compile(task: str):
    """Compile via the real chain and assert the contract is usable."""
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)
    adequacy = ContractAdequacyValidator().validate(requirements, contract)
    assert adequacy.status in {"adequate", "degraded"}, adequacy.reason_codes
    return contract


def _judge_evidence(contract, *, screen_text: str) -> list[dict]:
    """Model testimony for the [judge] criteria only.

    Deliberately omits `source` and never mentions app ids or ranks: a real
    model reports the text it can see, nothing more.
    """
    return [
        {
            "criterion": criterion.name,
            "screen_reference": f"mark_id={criterion.name}",
            "observed_value": screen_text,
        }
        for criterion in contract.success_criteria
        if not _is_self_observable(criterion)
    ]


def _evaluate(
    contract,
    *,
    screen_text: str,
    foreground: str = "tv.danmaku.bili",
    top_activity: str = "tv.danmaku.bili/.MainActivity",
    verifier_evidence: dict | None = None,
    checkable: bool | None = None,
):
    observation: dict = {"snapshot": {"current_app": foreground}}
    if checkable is not None:
        observation["screen_structures"] = [
            {
                "nodes": {
                    "n1": {"checkable": True, "checked": checkable, "visible": True}
                }
            }
        ]
    evidence = _judge_evidence(contract, screen_text=screen_text)
    return evaluate_finish_claim(
        contract=contract,
        verifier_evidence=verifier_evidence,
        after_observation=observation,
        device_signals={"top_activity": top_activity},
        finish_claim_matched=[item["criterion"] for item in evidence],
        reflect_named_evidence=evidence,
    )


# ----------------------------------------------------------------------
# search: semantic completion is judged from grounded screen evidence
# ----------------------------------------------------------------------


def test_search_task_uses_grounded_semantic_judgement_without_compiler_binding() -> None:
    contract = _compile("在哔哩哔哩搜索周杰伦")

    semantic = next(item for item in contract.success_criteria if item.name == "task_completed")
    assert semantic.predicate is None
    assert _evaluate(contract, screen_text="周杰伦的热门歌曲").status == "success"


def test_search_task_accepts_human_worded_app_and_no_app_testimony() -> None:
    """The model never reports the app; the system reads it from the device."""
    contract = _compile("在哔哩哔哩搜索周杰伦")

    assert _evaluate(contract, screen_text="周杰伦").status == "success"
    # Wrong app in the foreground still fails, whatever the screen text says.
    assert (
        _evaluate(
            contract,
            screen_text="周杰伦",
            foreground="com.tencent.mm",
            top_activity="com.tencent.mm/.Main",
        ).status
        == "failure"
    )


# ----------------------------------------------------------------------
# launch
# ----------------------------------------------------------------------


def test_launch_task_accepts_target_app_in_foreground() -> None:
    contract = _compile("打开哔哩哔哩")

    assert _evaluate(contract, screen_text="哔哩哔哩首页").status == "success"
    assert (
        _evaluate(
            contract,
            screen_text="哔哩哔哩首页",
            foreground="com.android.settings",
            top_activity="com.android.settings/.Main",
        ).status
        == "failure"
    )


# ----------------------------------------------------------------------
# toggle: the family that was 100% unfinishable
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "goal_state"),
    [("关闭蓝牙", False), ("开启wifi", True)],
)
def test_toggle_task_is_finishable(task: str, goal_state: bool) -> None:
    """Previously died at the adequacy gate before a single step ran."""
    contract = _compile(task)

    reached = _evaluate(
        contract,
        screen_text=task,
        foreground="com.android.settings",
        top_activity="com.android.settings/.Main",
        checkable=goal_state,
    )
    assert reached.status == "success", reached.evidence.get("per_criterion")

    not_reached = _evaluate(
        contract,
        screen_text=task,
        foreground="com.android.settings",
        top_activity="com.android.settings/.Main",
        checkable=not goal_state,
    )
    assert not_reached.status == "failure"


# ----------------------------------------------------------------------
# select: ordinal read from the verifier, not guessed by the model
# ----------------------------------------------------------------------


def test_select_task_accepts_verified_rank_and_rejects_wrong_rank() -> None:
    contract = _compile("在哔哩哔哩播放第三个视频")

    matched = _evaluate(
        contract,
        screen_text="第三个视频正在播放",
        verifier_evidence={
            "selected_object_signals": {
                "selected_object_match": True,
                "selected_object_expected_rank": 3,
            }
        },
    )
    assert matched.status == "success", matched.evidence.get("per_criterion")

    wrong_rank = _evaluate(
        contract,
        screen_text="第三个视频正在播放",
        verifier_evidence={
            "selected_object_signals": {
                "selected_object_match": True,
                "selected_object_expected_rank": 5,
            }
        },
    )
    assert wrong_rank.status == "failure"


# ----------------------------------------------------------------------
# Regressions: tasks that used to be rejected at step 0
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "关闭蓝牙",
        "开启wifi",
        "在美团点一杯咖啡不要加糖",
        "only use wifi to download",
        "打开设置把蓝牙关掉",
        "打开和平精英",
        "切换飞行模式",
    ],
)
def test_previously_undeployable_tasks_now_compile(task: str) -> None:
    """Toggle tasks and any task containing 不要/only used to be rejected
    before the first step. Compilation must now always proceed."""
    _compile(task)


@pytest.mark.parametrize(
    "task", ["在美团点一杯咖啡不要加糖", "only use wifi to download"]
)
def test_constrained_tasks_compile_without_code_side_gap(task: str) -> None:
    """S5: negative constraints are model-owned — code no longer reads task
    text for them, so a constrained task never registers a coverage gap."""
    requirements = TaskRequirementExtractor().extract(task)
    contract = HeuristicGoalCompiler().compile(task=task)

    adequacy = ContractAdequacyValidator().validate(requirements, contract)
    assert "constraints_uncovered" not in adequacy.reason_codes
    assert adequacy.status == "adequate"


def test_finish_still_blocked_without_any_evidence() -> None:
    """Fail-closed: no testimony and no claim must never pass."""
    contract = _compile("在哔哩哔哩搜索周杰伦")

    result = evaluate_finish_claim(
        contract=contract,
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=[],
        reflect_named_evidence=None,
    )
    assert result.status != "success"


def test_finish_blocked_when_evidence_is_ungrounded() -> None:
    """A placeholder reference carries no auditable provenance, so it cannot
    satisfy a criterion even when the reported value would match."""
    contract = _compile("在哔哩哔哩搜索周杰伦")

    evidence = [
        {
            "criterion": criterion.name,
            "screen_reference": "region-1",
            "observed_value": "周杰伦",
        }
        for criterion in contract.success_criteria
        if not _is_self_observable(criterion)
    ]
    result = evaluate_finish_claim(
        contract=contract,
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=[item["criterion"] for item in evidence],
        reflect_named_evidence=evidence,
    )
    assert result.status != "success"
