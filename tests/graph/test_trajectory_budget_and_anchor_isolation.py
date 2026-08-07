"""Item 6: trajectory-summary budget sync + kind-gated buckets; Item 7: anchor
isolation locks.

* 6a: ``trajectory_summary_steps`` must be same-source with the summary
  renderer — a truncated line is dropped whole, so ``allowed_steps`` is
  exactly the set of steps whose rows actually reach the judge.
* 6b: only ``effect_event`` / ``model_observation`` entries create buckets;
  anchor/seal/digest rows produce no empty ``sN: ?`` lines and never widen
  the referenceable step set.
* 7.1: anchors never appear in the trajectory summary or in
  ``criterion_history_from_ledger`` snapshots.
* 7.2: after ``remap_ledger_for_contract``, old-contract anchors cannot
  pollute a new contract's fold (name/key mismatch degrades fail-closed).
* 7.3: a later ``observed`` read overwrites the per-criterion anchor after a
  ``contradicted`` read → tier-2 acceptance recovers to ``satisfied``.

All deterministic; no FakeModel-style verdict stubs.
"""

from __future__ import annotations

import re

from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import fold_acceptance_verdicts
from phone_agent.graph.goal_evidence import (
    append_model_observations,
    criterion_history_from_ledger,
    criterion_semantic_key,
    model_observation_entry,
    remap_ledger_for_contract,
)
from phone_agent.graph.nodes.acceptance import (
    _trajectory_buckets,
    _trajectory_summary_for_judge,
    trajectory_summary_steps,
)

CONTRACT_ID = "contract-1"


def _judge_contract() -> GoalContract:
    """A vlm_judge contract: tier-2 (model screen-read) settles it."""
    return GoalContract(
        task_hash=CONTRACT_ID,
        redacted_objective="find target",
        objective_length=11,
        success_criteria=[
            SuccessCriterion(
                name="target",
                description="target visible",
                verification="vlm_judge",
            )
        ],
        compile_status="compiled",
    )


def _long_value() -> str:
    return "v" * 80


def _long_criterion(index: int) -> str:
    return f"criterion_long_name_{index}_" + "x" * 100


# ---------------------------------------------------------------------------
# Item 6a: truncation keeps allowed_steps same-source with rendered lines
# ---------------------------------------------------------------------------


def test_truncated_trajectory_allowed_steps_match_rendered_rows() -> None:
    """With observations long enough to blow the 2000-char budget, the steps
    that survive in the summary are exactly ``allowed_steps`` — no step whose
    row was truncated away stays referenceable."""
    ledger: list[dict] = []
    for step in range(12):
        ledger.append(
            {
                "kind": "effect_event",
                "contract_id": CONTRACT_ID,
                "action": f"Action{step}",
                "target": None,
                "observed_after": "verdict=success",
                "screen_id": f"s{step}",
                "step": step,
                "named_evidence": [],
                "semantic_keys": {},
            }
        )
        for index in range(3):
            ledger.append(
                model_observation_entry(
                    contract_id=CONTRACT_ID,
                    criterion=_long_criterion(index),
                    status="observed",
                    observed_value=_long_value(),
                    step=step,
                    screen_id=f"s{step}",
                    observation_epoch=step,
                )
            )

    summary = _trajectory_summary_for_judge(
        ledger, contract_id=CONTRACT_ID, lang="en", task_context=None
    )
    allowed = trajectory_summary_steps(ledger, contract_id=CONTRACT_ID, lang="en")

    assert len(summary) <= 2000
    assert allowed  # 预算确实装下了部分行
    assert len(allowed) < 12  # 截断真实发生：不是全部 step 都可引用
    # 集合 = 渲染行对应 step：每个 allowed step 的行都在摘要里……
    for step in allowed:
        assert f"s{step}:" in summary
    # ……且摘要里出现的行 step 恰好是 allowed（严格同源，无多余可引用 step）
    rendered_steps = {int(m) for m in re.findall(r"s(\d+):", summary)}
    assert rendered_steps == allowed


# ---------------------------------------------------------------------------
# Item 6b: kind-gated buckets — anchors/seals/digest rows never build buckets
# ---------------------------------------------------------------------------


def test_anchor_and_seal_entries_never_create_trajectory_buckets() -> None:
    """Only effect_event / model_observation entries may create a bucket. An
    observation_anchor or stage_seal row must not produce an empty ``sN: ?``
    summary line nor widen allowed_steps."""
    ledger: list[dict] = [
        {
            "kind": "effect_event",
            "contract_id": CONTRACT_ID,
            "action": "Tap",
            "target": None,
            "observed_after": "verdict=success",
            "screen_id": "s1",
            "step": 1,
            "named_evidence": [],
            "semantic_keys": {},
        },
        {
            **model_observation_entry(
                contract_id=CONTRACT_ID,
                criterion="target",
                status="observed",
                step=5,
                screen_id="s5",
                observation_epoch=5,
            ),
            "kind": "observation_anchor",  # anchor 形态：仅 kind 不同
        },
        {
            "kind": "stage_seal",
            "contract_id": CONTRACT_ID,
            "stage_id": "stage-0",
            "criteria_sealed": ["target"],
            "screen_id": "s7",
            "step": 7,
        },
        {
            "kind": "model_observation",
            "contract_id": CONTRACT_ID,
            "criterion": "target",
            "status": "observed",
            "observed_value": "yes",
            "step": 3,
            "screen_id": "s3",
            "observation_epoch": 3,
        },
    ]

    buckets = _trajectory_buckets(ledger, contract_id=CONTRACT_ID)
    assert set(buckets) == {1, 3}  # 锚点 step5 / seal step7 不建桶
    summary = _trajectory_summary_for_judge(
        ledger, contract_id=CONTRACT_ID, lang="zh", task_context=None
    )
    assert "s5:" not in summary and "s7:" not in summary
    assert "s1:" in summary and "s3:" in summary
    assert trajectory_summary_steps(ledger, contract_id=CONTRACT_ID) == {1, 3}


# ---------------------------------------------------------------------------
# Item 7.1: anchors stay out of the liveness consumer (criterion history)
# ---------------------------------------------------------------------------


def test_criterion_history_unaffected_by_anchor_entries() -> None:
    """criterion_history_from_ledger groups model screen-reads; anchor rows
    (same screen/epoch key) neither add a snapshot nor write per_criterion."""
    ledger: list[dict] = [
        model_observation_entry(
            contract_id=CONTRACT_ID,
            criterion="target",
            status="observed",
            observed_value="yes",
            step=1,
            screen_id="s1",
            observation_epoch=1,
        ),
        {
            **model_observation_entry(
                contract_id=CONTRACT_ID,
                criterion="target",
                status="observed",
                observed_value="yes",
                step=1,
                screen_id="s1",
                observation_epoch=1,
            ),
            "kind": "observation_anchor",
        },
    ]

    history = criterion_history_from_ledger(ledger, contract_id=CONTRACT_ID)
    assert len(history) == 1  # 锚点不产生第二个 snapshot
    assert history[0]["per_criterion"] == {"target": "observed"}


# ---------------------------------------------------------------------------
# Item 7.2: old-contract anchors cannot pollute a remapped new contract
# ---------------------------------------------------------------------------


def _remap_ledger_with_key(*, semantic_key: str | None) -> list[dict]:
    entry = model_observation_entry(
        contract_id=CONTRACT_ID,
        criterion="oldname",
        status="observed",
        observed_value="yes",
        step=1,
        screen_id="s1",
        observation_epoch=1,
        semantic_key=semantic_key,
    )
    anchor = {**entry, "kind": "observation_anchor"}
    contract = _judge_contract()
    return remap_ledger_for_contract(
        [entry, anchor],
        contract_id=CONTRACT_ID,
        criteria={item.name: item for item in contract.success_criteria},
        task_plan=None,
    )


def test_remap_keeps_unmatched_old_anchor_fail_closed() -> None:
    """An old-contract anchor whose semantic key matches nothing in the new
    contract stays as-is (name unchanged) — the new fold must NOT see it as a
    satisfied read for its criterion (no cross-contract pollution)."""
    remapped = _remap_ledger_with_key(semantic_key="old-key-not-in-new-contract")
    assert all(entry.get("criterion") == "oldname" for entry in remapped)

    fold = fold_acceptance_verdicts(
        contract=_judge_contract(),
        ledger=remapped,
        contract_id=CONTRACT_ID,
        screen_id="s9",
        observation_epoch=9,
        current_step=9,
    )
    assert fold["per_criterion"]["target"]["status"] == "unknown"
    assert fold["overall"] != "satisfied"


def test_remap_renames_matching_anchor_into_new_contract() -> None:
    """When the anchor's semantic key DOES match a current criterion, remap
    renames it to the current name and the fold settles satisfied — the
    inheritance path still works end to end."""
    contract = _judge_contract()
    matching_key = criterion_semantic_key(
        contract.success_criteria[0].description
    )
    remapped = _remap_ledger_with_key(semantic_key=matching_key)
    assert all(entry.get("criterion") == "target" for entry in remapped)

    fold = fold_acceptance_verdicts(
        contract=contract,
        ledger=remapped,
        contract_id=CONTRACT_ID,
        screen_id="s9",
        observation_epoch=9,
        current_step=9,
    )
    assert fold["per_criterion"]["target"]["status"] == "satisfied"
    assert fold["overall"] == "satisfied"


# ---------------------------------------------------------------------------
# Item 7.3: a later observed read overwrites the anchor after contradicted
# ---------------------------------------------------------------------------


def test_observed_after_contradicted_restores_tier2_satisfied() -> None:
    """contradicted → observed: the newest read overwrites the per-criterion
    anchor, so tier-2 recovers to satisfied (positive recovery; contradicted
    is not sticky across a real counter-observation)."""
    ledger = append_model_observations(
        [],
        contract_id=CONTRACT_ID,
        observations=[{"criterion": "target", "status": "observed"}],
        step=1,
        screen_id="s1",
        observation_epoch=1,
    )
    ledger = append_model_observations(
        ledger,
        contract_id=CONTRACT_ID,
        observations=[{"criterion": "target", "status": "contradicted"}],
        step=2,
        screen_id="s2",
        observation_epoch=2,
    )
    # 先确认 contradicted 时确实锁死
    blocked = fold_acceptance_verdicts(
        contract=_judge_contract(),
        ledger=ledger,
        contract_id=CONTRACT_ID,
        screen_id="s2",
        observation_epoch=2,
        current_step=2,
    )
    assert blocked["per_criterion"]["target"]["status"] == "contradicted"

    ledger = append_model_observations(
        ledger,
        contract_id=CONTRACT_ID,
        observations=[{"criterion": "target", "status": "observed"}],
        step=3,
        screen_id="s3",
        observation_epoch=3,
    )
    fold = fold_acceptance_verdicts(
        contract=_judge_contract(),
        ledger=ledger,
        contract_id=CONTRACT_ID,
        screen_id="s3",
        observation_epoch=3,
        current_step=3,
    )
    assert fold["per_criterion"]["target"]["status"] == "satisfied"
    assert fold["per_criterion"]["target"]["reason"] == "model_observed"
    assert fold["overall"] == "satisfied"
