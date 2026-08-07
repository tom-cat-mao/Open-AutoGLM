"""Tests for the GoalEvaluator (Phase 3)."""

import pytest

from phone_agent.graph.goal import GoalContract, SuccessCriterion
from phone_agent.graph.goal_evaluator import evaluate_finish_claim


def _contract(criteria, **kwargs):
    return GoalContract(
        task_hash="h",
        redacted_objective="obj",
        objective_length=3,
        success_criteria=criteria,
        verification_strategy=kwargs.get("strategy", "hybrid"),
        target_app_hint=kwargs.get("app_hint"),
        ordinal=kwargs.get("ordinal"),
        compile_status="compiled",
        compile_source="external",
    )


# ----------------------------------------------------------------------
# accessibility_text_match
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("criterion_name", "description", "marks_text", "expected_status", "in_matched", "in_missing"),
    (
        ("title", "stub", "视频标题一", "success", {"title"}, set()),
        ("title", "missing", "other text", "unknown", set(), set()),
        ("search_query_visible", "raw", "搜索结果：村长托马斯", "success", {"search_query_visible"}, set()),
    ),
)
def test_accessibility_text_match_states(
    criterion_name, description, marks_text, expected_status, in_matched, in_missing
) -> None:
    import hashlib

    if description == "stub":
        stub = f"sha256:{hashlib.sha256(marks_text.encode('utf-8')).hexdigest()[:12]}"
        description = f"screen shows {stub}"
    elif description == "missing":
        description = "screen shows sha256:0123456789ab"
    elif description == "raw":
        description = marks_text
    contract = _contract([
        SuccessCriterion(name=criterion_name, description=description, verification="accessibility_text_match"),
    ])
    result = evaluate_finish_claim(
        contract=contract, after_observation={"marks": [{"text_summary": marks_text}]}
    )
    assert result.status == expected_status
    assert in_matched <= set(result.matched)
    assert in_missing <= set(result.missing)
    if expected_status == "unknown":
        assert criterion_name not in result.missing
        assert criterion_name not in result.matched


# ----------------------------------------------------------------------
# object_rank_match
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ordinal", "expected_rank", "expected_status", "in_matched", "in_missing"),
    (
        (2, 2, "success", {"rank_2"}, set()),
        (2, 1, "failure", set(), {"rank_2"}),
        (None, None, "failure", set(), {"rank_none"}),
    ),
)
def test_object_rank_match_states(
    ordinal, expected_rank, expected_status, in_matched, in_missing
) -> None:
    criterion_name = "rank_none" if ordinal is None else "rank_2"
    contract = _contract(
        [
            SuccessCriterion(name=criterion_name, description="rank", verification="object_rank_match"),
        ],
        ordinal=ordinal,
    )
    evidence = {
        "selected_object_signals": {
            "selected_object_match": True,
            "selected_object_expected_rank": expected_rank,
        }
    }
    result = evaluate_finish_claim(contract=contract, verifier_evidence=evidence)
    assert result.status == expected_status
    assert in_matched <= set(result.matched)
    assert in_missing <= set(result.missing)


# ----------------------------------------------------------------------
# vlm_judge — three-part check
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("finish_matched", "reflect", "expected_status"),
    (
        # Finish-claim naming retired (Fix A): an unnamed criterion is not
        # missing; with the VLM unconsulted it stays unknown (fail-closed).
        ([], None, "unknown"),
        (["c"], None, "unknown"),
        (["c"], [], "failure"),
        (["c"], [{"criterion": "c", "screen_reference": "mark_id=player"}], "success"),
    ),
)
def test_vlm_judge_three_part_check(
    finish_matched, reflect, expected_status
) -> None:
    """The vlm_judge check: VLM consulted / grounded evidence. Finish-claim
    naming no longer gates (Fix A); each state (unknown / failure / success)
    is a distinct case of the same evaluate_finish_claim path."""
    contract = _contract([
        SuccessCriterion(name="c", description="visible", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract, finish_claim_matched=finish_matched, reflect_named_evidence=reflect
    )
    assert result.status == expected_status
    if expected_status == "unknown":
        assert "c" not in result.missing
        assert "c" not in result.matched
    elif expected_status == "failure":
        assert "c" in result.missing
    else:
        assert "c" in result.matched


# ----------------------------------------------------------------------
# vlm_judge — criterion-name normalization (W1-A: format drift only)
# ----------------------------------------------------------------------


def test_normalize_criterion_name_collapses_only_format_drift() -> None:
    from phone_agent.graph.goal_evaluator import _normalize_criterion_name

    cases = {
        "flight_search_parameters": "flight_search_parameters",
        "Flight Search Parameters": "flight_search_parameters",
        "flight-search-parameters": "flight_search_parameters",
        "  Flight   Search___Parameters  ": "flight_search_parameters",
        # No separator between words: this is NOT a format drift — words merge
        # and the name must stay unmatched (no fuzzy/semantic matching).
        "CheapestFlightResult": "cheapestflightresult",
        "": "",
        None: "",
        "---": "",
    }
    for raw, expected in cases.items():
        assert _normalize_criterion_name(raw) == expected


@pytest.mark.parametrize(
    ("raw_criterion", "criterion_name"),
    (
        ("Flight Search Parameters", "flight_search_parameters"),
        ("cheapest-flight-result", "cheapest_flight_result"),
    ),
)
def test_vlm_judge_format_drift_normalizes_to_match(raw_criterion, criterion_name) -> None:
    contract = _contract([
        SuccessCriterion(name=criterion_name, description="params", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=[criterion_name],
        reflect_named_evidence=[
            {"criterion": raw_criterion, "screen_reference": "mark_id=row"}
        ],
    )
    assert result.status == "success"
    assert criterion_name in result.matched


def test_vlm_judge_typed_predicate_accepts_drifted_criterion_name() -> None:
    """The typed-predicate path uses the same normalized map lookup."""
    from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

    contract = _contract([
        SuccessCriterion(
            name="topic",
            description="target content visible",
            verification="vlm_judge",
            predicate=CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", "周杰伦"),
        ),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["topic"],
        reflect_named_evidence=[
            {
                "criterion": "Topic",
                "screen_reference": "mark_id=title",
                "observed_value": "周杰伦",
                "source": "mark",
            }
        ],
    )
    assert result.status == "success"
    assert "topic" in result.matched


def test_vlm_judge_out_of_whitelist_name_stays_missing_fail_closed() -> None:
    """A judge name outside the contract whitelist is ignored for matching and
    recorded for trace diagnosis; the criterion stays missing (fail-closed)."""
    contract = _contract([
        SuccessCriterion(name="flight_search_parameters", description="params", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["flight_search_parameters"],
        reflect_named_evidence=[
            {"criterion": "search form visible", "screen_reference": "mark_id=form"}
        ],
    )
    assert result.status == "failure"
    assert "flight_search_parameters" in result.missing
    assert (result.evidence or {}).get("named_evidence_ignored") == [
        "search form visible"
    ]


def test_vlm_judge_ignored_names_never_satisfy_other_criteria() -> None:
    contract = _contract([
        SuccessCriterion(name="a", description="", verification="vlm_judge"),
        SuccessCriterion(name="b", description="", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["a", "b"],
        reflect_named_evidence=[
            {"criterion": "B", "screen_reference": "mark_id=1"},
            {"criterion": "totally unrelated name", "screen_reference": "mark_id=2"},
        ],
    )
    # Only "b" carries grounded evidence; "a" and the stray name stay missing.
    assert result.status == "failure"
    assert result.matched == ["b"]
    assert set(result.missing) == {"a"}
    assert (result.evidence or {}).get("named_evidence_ignored") == [
        "totally unrelated name"
    ]


def test_vlm_judge_duplicate_normalized_names_keep_first_grounded_item() -> None:
    contract = _contract([
        SuccessCriterion(name="task_completed", description="", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["task_completed"],
        reflect_named_evidence=[
            {"criterion": "Task Completed", "screen_reference": "mark_id=good"},
            {"criterion": "task completed", "screen_reference": "placeholder_screen"},
        ],
    )
    assert result.status == "success"


def test_vlm_judge_missing_any_required_criterion_fails() -> None:
    """completed=true must cover every required [judge] criterion; a missing
    one keeps the gate closed."""
    contract = _contract([
        SuccessCriterion(name="a", description="", verification="vlm_judge"),
        SuccessCriterion(name="b", description="", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["a", "b"],
        reflect_named_evidence=[
            {"criterion": "a", "screen_reference": "mark_id=1"},
        ],
    )
    assert result.status == "failure"
    assert "b" in result.missing


# ----------------------------------------------------------------------
# Programmatic contradiction overrides vlm_judge
# ----------------------------------------------------------------------


def test_programmatic_missing_overrides_vlm_judge_same_name() -> None:
    contract = _contract([
        SuccessCriterion(name="app_visible", description="test", verification="app_or_activity_match"),
        SuccessCriterion(name="app_visible", description="test", verification="vlm_judge"),
    ])
    # app_or_activity_match will fail (no app_hint in contract → missing)
    # vlm_judge will match (named + grounded)
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["app_visible"],
        reflect_named_evidence=[{"criterion": "app_visible", "screen_reference": "mark=x"}],
    )
    assert "app_visible" in result.missing
    assert result.status == "failure"


# ----------------------------------------------------------------------
# Mixed criteria: object_rank_match + vlm_judge
# ----------------------------------------------------------------------


def test_mixed_programmatic_and_vlm_judge_both_matched_is_success() -> None:
    contract = _contract(
        [
            SuccessCriterion(name="player_visible", description="", verification="vlm_judge"),
            SuccessCriterion(name="rank_2", description="", verification="object_rank_match"),
        ],
        ordinal=2,
    )
    evidence = {"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": 2}}
    result = evaluate_finish_claim(
        contract=contract,
        verifier_evidence=evidence,
        finish_claim_matched=["player_visible"],
        reflect_named_evidence=[{"criterion": "player_visible", "screen_reference": "m"}],
    )
    assert result.status == "success"
    assert set(result.matched) == {"player_visible", "rank_2"}


def test_mixed_with_one_failure_is_failure() -> None:
    contract = _contract(
        [
            SuccessCriterion(name="player_visible", description="", verification="vlm_judge"),
            SuccessCriterion(name="rank_2", description="", verification="object_rank_match"),
        ],
        ordinal=2,
    )
    # rank_2 has wrong rank
    evidence = {"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": 1}}
    result = evaluate_finish_claim(
        contract=contract,
        verifier_evidence=evidence,
        finish_claim_matched=["player_visible"],
        reflect_named_evidence=[{"criterion": "player_visible", "screen_reference": "m"}],
    )
    assert result.status == "failure"
    assert "rank_2" in result.missing


# ----------------------------------------------------------------------
# app_or_activity_match
# ----------------------------------------------------------------------


def test_app_or_activity_match_success_by_package() -> None:
    contract = _contract([
        SuccessCriterion(name="app_open", description="target app", verification="app_or_activity_match"),
    ], app_hint="bilibili")
    from phone_agent.config.apps import APP_PACKAGES
    contract_pkg = APP_PACKAGES.get("bilibili")
    obs = {"snapshot": {"current_app": contract_pkg + "/.MainActivity"}}
    result = evaluate_finish_claim(contract=contract, after_observation=obs)
    assert result.status == "success"


def test_app_or_activity_match_failure_wrong_app() -> None:
    contract = _contract([
        SuccessCriterion(name="app_open", description="target app", verification="app_or_activity_match"),
    ], app_hint="settings")
    obs = {"snapshot": {"current_app": "com.example.other/.Main"}}
    result = evaluate_finish_claim(contract=contract, after_observation=obs)
    assert result.status == "failure"


# ----------------------------------------------------------------------
# external_probe
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probe_result", "expected_status"),
    ((True, "success"), (False, "failure"), (None, "failure")),
)
def test_external_probe_outcomes(probe_result, expected_status) -> None:
    contract = _contract([
        SuccessCriterion(name="probe", description="", verification="external_probe", probe_id="test_probe"),
    ])
    goal_probes = {} if probe_result is None else {"test_probe": lambda: probe_result}
    result = evaluate_finish_claim(
        contract=contract,
        goal_probes=goal_probes,
    )
    assert result.status == expected_status


# ----------------------------------------------------------------------
# Fail-closed: unknown never auto-upgrades
# ----------------------------------------------------------------------


def test_unknown_never_upgrades_to_success() -> None:
    contract = _contract([
        SuccessCriterion(name="c", description="", verification="vlm_judge"),
    ])
    # VLM not run, named in finish → unknown (not success)
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["c"],
        reflect_named_evidence=None,
    )
    assert result.status == "unknown"


# ----------------------------------------------------------------------
# Self-observable criteria are settled from device truth, not testimony
# ----------------------------------------------------------------------


def _foreground_contract():
    from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

    return _contract(
        [
            SuccessCriterion(
                name="app_open",
                description="target app",
                verification="app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "bilibili"
                ),
            )
        ],
        app_hint="bilibili",
    )


def test_self_observable_criterion_ignores_human_worded_report() -> None:
    """The model naming the app in human words used to fail the whole task:
    the gate string-compared its answer against a canonical id it never saw."""
    result = evaluate_finish_claim(
        contract=_foreground_contract(),
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=["app_open"],
        reflect_named_evidence=[
            {
                "criterion": "app_open",
                "screen_reference": "fg",
                "observed_value": "哔哩哔哩",
            }
        ],
    )
    assert result.status == "success"


def test_self_observable_criterion_does_not_need_to_be_reported() -> None:
    """Absent model evidence is not counter-evidence when the system can read
    the fact itself."""
    result = evaluate_finish_claim(
        contract=_foreground_contract(),
        after_observation={"snapshot": {"current_app": "tv.danmaku.bili"}},
        device_signals={"top_activity": "tv.danmaku.bili/.MainActivity"},
        finish_claim_matched=[],
        reflect_named_evidence=[],
    )
    assert result.status == "success"


def test_self_observable_criterion_still_fails_on_device_truth() -> None:
    """Reading truth directly must not weaken the gate: a wrong foreground app
    fails even when the model insists the right one is showing."""
    result = evaluate_finish_claim(
        contract=_foreground_contract(),
        after_observation={"snapshot": {"current_app": "com.tencent.mm"}},
        device_signals={"top_activity": "com.tencent.mm/.Main"},
        finish_claim_matched=["app_open"],
        reflect_named_evidence=[
            {
                "criterion": "app_open",
                "screen_reference": "fg",
                "observed_value": "bilibili",
            }
        ],
    )
    assert result.status == "failure"


def test_only_raw_text_criteria_need_model_judgement() -> None:
    """Raw-text criteria remain the model's call; structural facts do not."""
    from phone_agent.graph.goal_evaluator import _is_self_observable
    from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

    semantic = SuccessCriterion(
        name="topic",
        description="target content visible",
        verification="vlm_judge",
        predicate=CORE_PREDICATE_CATALOG.create_spec(
            "semantic.entity_matches", "周杰伦"
        ),
    )
    assert not _is_self_observable(semantic)

    for verification, predicate_id, value in (
        ("app_or_activity_match", "app.foreground_identity", "bilibili"),
        ("object_rank_match", "ui.object_rank", 3),
        ("toggle_state_match", "ui.toggle_state", False),
        ("focus_or_keyboard", "ui.focused", True),
    ):
        criterion = SuccessCriterion(
            name=verification,
            description="",
            verification=verification,
            predicate=CORE_PREDICATE_CATALOG.create_spec(predicate_id, value),
        )
        assert _is_self_observable(criterion), verification


# ----------------------------------------------------------------------
# Fix A: finish-claim naming retired — the ledger is the evidence authority
# ----------------------------------------------------------------------


def test_vlm_judge_unnamed_but_grounded_evidence_satisfies() -> None:
    """Fix A: a vlm_judge criterion is satisfied by grounded reflect evidence
    even when the finish claim never named it (naming gate retired)."""
    contract = _contract([
        SuccessCriterion(name="c", description="visible", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=[],
        reflect_named_evidence=[
            {"criterion": "c", "screen_reference": "mark_id=player"}
        ],
    )
    assert result.status == "success"
    assert "c" in result.matched


def test_pure_evaluator_unnamed_observed_matches() -> None:
    """Fix A: PureGoalEvaluator no longer marks an unnamed criterion missing —
    the typed ledger evidence settles it."""
    from phone_agent.graph.goal_evaluator import PureGoalEvaluator
    from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

    contract = GoalContract(
        task_hash="c1",
        redacted_objective="open app",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(
                name="app",
                description="app foreground",
                verification="app_or_activity_match",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "app.foreground_identity", "settings"
                ),
            )
        ],
        compile_status="compiled",
    )
    ledger = [
        {
            "criterion_id": "app",
            "predicate_id": "app.foreground_identity",
            "status": "matched",
            "reason_code": "values_match",
            "source_kind": "device",
            "confidence_bucket": "high",
            "contract_id": "c1",
            "screen_id": "screen-1",
            "observation_epoch": 2,
        }
    ]
    result = PureGoalEvaluator().evaluate(
        contract=contract,
        contract_id=contract.task_hash,
        evidence_ledger=ledger,
        finish_claim_matched=[],  # naming retired (Fix A)
        screen_id="screen-1",
        observation_epoch=2,
    )
    assert result.status == "success"
    assert "app" in result.matched
