"""Progress evidence review: claims, exhaustion, and resource disclosure."""

import pytest

from phone_agent.config.policy import (
    PROGRESS_CLAIM_MAX_ROUNDS,
    PROGRESS_EXHAUSTION_TRIGGER,
)
from phone_agent.graph.context import (
    build_budget_section,
    progress_claim_rejection_update,
    progress_exhaustion,
    validate_progress_claim,
)


def _contract(runtime_reference: str = "r1", names=("c1", "c2")) -> dict:
    return {
        "runtime_reference": runtime_reference,
        "success_criteria": [
            {"name": name, "verification": "vlm_judge"} for name in names
        ],
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


def _entry(epoch, criterion, status, *, target_app_entered=None, kind="evaluation") -> dict:
    return {
        "kind": kind,
        "contract_id": "r1",
        "criterion_id": criterion,
        "status": status,
        "screen_id": f"s{epoch}",
        "observation_epoch": epoch,
        "step": epoch,
        "target_app_entered": target_app_entered,
    }


def _model_obs(epoch, criterion, status, digest=None) -> dict:
    return {
        "kind": "model_observation",
        "contract_id": "r1",
        "criterion": criterion,
        "status": status,
        "observed_value_digest": digest,
        "screen_id": f"s{epoch}",
        "observation_epoch": epoch,
        "step": epoch,
    }


def _effect(epoch) -> dict:
    return {
        "kind": "effect_event",
        "contract_id": "r1",
        "action": "Tap",
        "screen_id": f"s{epoch}",
        "step": epoch,
    }


def _state(**overrides) -> dict:
    state = {
        "goal_contract": _contract(),
        "goal_evidence_ledger": [],
        "gui_memory": {
            "task_progress": {},
            "screen_transition_stream": [],
            "tried_actions": [],
        },
        "max_steps": 10,
        "step_cap": 10,
        "continuation_count": 0,
        "step_count": 10,
        "locate_count": 0,
        "progress_exhaustion_streak": 0,
        "progress_claim_round_count": 0,
        "progress_claim_grace_steps_remaining": 0,
        "progress_declaration_due": False,
    }
    state.update(overrides)
    return state


def test_validate_progress_claim_accepts_criterion_rank_up() -> None:
    ledger = [
        _entry(1, "c1", "unknown"),
        _entry(2, "c1", "matched"),
    ]
    result = validate_progress_claim(
        _state(goal_evidence_ledger=ledger),
        {"summary": "推进中", "evidence_refs": ["criterion:c1"]},
    )

    assert result["status"] == "accepted"
    assert "criterion_rank_up" in result["reason"]


def test_validate_progress_claim_rejects_aba_oscillation() -> None:
    """A-B-A-B status oscillation (matched↔missing) is not net progress."""
    ledger = [
        _entry(1, "c1", "matched"),
        _entry(2, "c1", "missing"),
        _entry(3, "c1", "matched"),
        _entry(4, "c1", "missing"),
    ]
    result = validate_progress_claim(
        _state(goal_evidence_ledger=ledger),
        {"summary": "推进中", "evidence_refs": ["criterion:c1"]},
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "no_strong_evidence"


def test_validate_progress_claim_rejects_infinite_scroll_novel_states() -> None:
    stream = [
        {"surface": "Feed", "screen_id": f"screen-{index}"}
        for index in range(6)
    ]
    result = validate_progress_claim(
        _state(
            goal_evidence_ledger=[],
            gui_memory={"screen_transition_stream": stream, "task_progress": {}},
        ),
        {"summary": "一直在下滑找内容", "evidence_refs": ["screen:screen-5"]},
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "no_strong_evidence"


def test_validate_progress_claim_rejects_empty_claim() -> None:
    result = validate_progress_claim(_state(), {})

    assert result["status"] == "rejected"
    assert result["missing"] == ["progress_claim"]


def test_validate_progress_claim_accepts_value_digest_change() -> None:
    ledger = [
        _model_obs(1, "form_value", "observed", digest="old"),
        _model_obs(2, "form_value", "observed", digest="new"),
    ]
    result = validate_progress_claim(
        _state(goal_evidence_ledger=ledger),
        {"summary": "表单值已变化", "evidence_refs": ["criterion:form_value"]},
    )

    assert result["status"] == "accepted"
    assert result["reason"] == "fresh_observation_value"


def test_progress_claim_round_cap_exhausts_after_rejections() -> None:
    state = _state(progress_claim_round_count=PROGRESS_CLAIM_MAX_ROUNDS - 1)
    update = progress_claim_rejection_update(
        state,
        {"status": "rejected", "missing": ["strong_progress_evidence"], "reason": "no_strong_evidence"},
    )

    assert update["progress_claim_round_count"] == PROGRESS_CLAIM_MAX_ROUNDS
    assert update["finished"] is True
    assert update["failure_cause"] == "progress_evidence_exhausted"


def test_progress_exhaustion_dry_reaches_declaration_due() -> None:
    state = _state(
        step_count=6,
        progress_exhaustion_streak=PROGRESS_EXHAUSTION_TRIGGER - 1,
        gui_memory={
            "screen_transition_stream": [
                {"surface": "A", "screen_id": "same"} for _ in range(6)
            ],
            "tried_actions": [{"action": "Tap", "surface": "A", "had_effect": False}],
            "task_progress": {},
        },
    )
    result = progress_exhaustion(state)

    assert result["dry"] is True
    assert result["streak"] == PROGRESS_EXHAUSTION_TRIGGER
    assert result["declaration_due"] is True


def test_progress_exhaustion_non_dry_on_form_value_change() -> None:
    state = _state(
        goal_evidence_ledger=[
            _model_obs(1, "form_value", "observed", digest="old"),
            _model_obs(2, "form_value", "observed", digest="new"),
        ],
        step_count=2,
        progress_exhaustion_streak=3,
        gui_memory={
            "screen_transition_stream": [{"surface": "Form", "screen_id": "s1"}],
            "tried_actions": [{"action": "Type", "surface": "Form", "had_effect": True}],
            "task_progress": {},
        },
    )
    result = progress_exhaustion(state)

    assert result["dry"] is False
    assert result["streak"] == 0


def test_progress_exhaustion_long_novel_list_still_dry_without_goal_evidence() -> None:
    stream = [
        {"surface": "Feed", "screen_id": f"screen-{index}"}
        for index in range(8)
    ]
    result = progress_exhaustion(
        _state(
            step_count=8,
            gui_memory={
                "screen_transition_stream": stream,
                "tried_actions": [{"action": "Swipe", "surface": "Feed", "had_effect": False}],
                "task_progress": {},
            },
        )
    )

    assert result["dry"] is True
    assert "novel_state_without_goal_evidence" in result["reasons"]


# ----------------------------------------------------------------------
# Budget visibility section (F2.2)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "budget_state", "present", "absent"),
    [
        (
            "cn",
            {"step_cap": 20, "step_count": 5},
            ("已用 5/20 步", "剩余 15 步"),
            ("progress_claim",),
        ),
        (
            "en",
            {"step_cap": 20, "step_count": 15},
            ("used 15/20 steps", "resource limit", "Trajectory hint"),
            (),
        ),
    ],
)
def test_budget_section_reports_step_cap_tiers(
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
    assert build_budget_section({"step_cap": 0, "step_count": 0}, lang="cn") == ""


def test_budget_section_omits_locate_countdown() -> None:
    """Effect-guards: the locate budget is a runaway fuse, not a scarcity
    budget — the "locate 剩余 x/3" countdown is gone from both languages (the
    scarcity hint actively induced the model to abandon mid-task)."""
    block = build_budget_section(
        {"step_cap": 20, "step_count": 5, "locate_count": 1},
        lang="cn",
    )

    assert "locate" not in block

    block_en = build_budget_section(
        {"step_cap": 20, "step_count": 5, "locate_count": 3},
        lang="en",
    )

    assert "locate" not in block_en


def test_budget_section_locate_line_absent_without_max_steps() -> None:
    assert build_budget_section({"step_cap": 0, "locate_count": 0}, lang="cn") == ""


def test_budget_section_tier3_requests_progress_claim() -> None:
    block = build_budget_section(
        {
            "step_cap": 20,
            "step_count": 19,
            "progress_declaration_due": True,
            "progress_exhaustion_streak": 4,
        },
        lang="cn",
    )

    assert "progress_claim" in block
    assert "系统只认账本证据" in block
