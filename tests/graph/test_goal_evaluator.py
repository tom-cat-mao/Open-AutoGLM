"""Tests for the GoalEvaluator (Phase 3)."""

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


def test_accessibility_text_match_matches_when_sha256_stub_present() -> None:
    import hashlib

    text = "视频标题一"
    stub = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
    contract = _contract([
        SuccessCriterion(name="title", description=f"screen shows {stub}", verification="accessibility_text_match"),
    ])
    obs = {"marks": [{"text_summary": text}]}
    result = evaluate_finish_claim(contract=contract, after_observation=obs)
    assert result.status == "success"
    assert "title" in result.matched


def test_accessibility_text_match_fails_when_text_not_found() -> None:
    contract = _contract([
        SuccessCriterion(name="title", description="screen shows sha256:0123456789ab", verification="accessibility_text_match"),
    ])
    obs = {"marks": [{"text_summary": "other text"}]}
    result = evaluate_finish_claim(contract=contract, after_observation=obs)
    assert result.status == "failure"
    assert "title" in result.missing


def test_accessibility_text_match_uses_raw_visible_text() -> None:
    contract = _contract([
        SuccessCriterion(
            name="search_query_visible",
            description="村长托马斯",
            verification="accessibility_text_match",
        ),
    ])
    observation = {"marks": [{"text_summary": "搜索结果：村长托马斯"}]}

    result = evaluate_finish_claim(contract=contract, after_observation=observation)

    assert result.status == "success"
    assert result.matched == ["search_query_visible"]


# ----------------------------------------------------------------------
# object_rank_match
# ----------------------------------------------------------------------


def test_object_rank_match_success() -> None:
    contract = _contract(
        [
            SuccessCriterion(name="rank_2", description="2nd item", verification="object_rank_match"),
        ],
        ordinal=2,
    )
    evidence = {"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": 2}}
    result = evaluate_finish_claim(contract=contract, verifier_evidence=evidence)
    assert result.status == "success"


def test_object_rank_match_mismatch() -> None:
    contract = _contract(
        [
            SuccessCriterion(name="rank_2", description="2nd item", verification="object_rank_match"),
        ],
        ordinal=2,
    )
    # expected_rank = 1 in signals but ordinal = 2
    evidence = {"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": 1}}
    result = evaluate_finish_claim(contract=contract, verifier_evidence=evidence)
    assert result.status == "failure"
    assert "rank_2" in result.missing


def test_object_rank_match_with_null_ordinal_is_missing() -> None:
    """P2-2: ordinal=None must not spuriously match when expected_rank is also None."""
    contract = _contract(
        [
            SuccessCriterion(name="rank_none", description="no ordinal", verification="object_rank_match"),
        ],
        ordinal=None,
    )
    evidence = {"selected_object_signals": {"selected_object_match": True, "selected_object_expected_rank": None}}
    result = evaluate_finish_claim(contract=contract, verifier_evidence=evidence)
    assert result.status == "failure"
    assert "rank_none" in result.missing


# ----------------------------------------------------------------------
# vlm_judge — three-part check
# ----------------------------------------------------------------------


def test_vlm_judge_not_named_in_finish_is_failure() -> None:
    contract = _contract([
        SuccessCriterion(name="c", description="visible", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract, finish_claim_matched=[], reflect_named_evidence=None
    )
    assert result.status == "failure"
    assert "c" in result.missing


def test_vlm_judge_vlm_not_run_is_unknown_not_failure() -> None:
    contract = _contract([
        SuccessCriterion(name="c", description="visible", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["c"],
        reflect_named_evidence=None,  # VLM not consulted yet
    )
    assert result.status == "unknown"
    assert "c" not in result.missing
    assert "c" not in result.matched


def test_vlm_judge_vlm_ran_no_evidence_is_failure() -> None:
    contract = _contract([
        SuccessCriterion(name="c", description="visible", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["c"],
        reflect_named_evidence=[],  # VLM ran but returned no evidence
    )
    assert result.status == "failure"
    assert "c" in result.missing


def test_vlm_judge_with_grounded_evidence_is_success() -> None:
    contract = _contract([
        SuccessCriterion(name="player_visible", description="player", verification="vlm_judge"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        finish_claim_matched=["player_visible"],
        reflect_named_evidence=[{"criterion": "player_visible", "screen_reference": "mark_id=player"}],
    )
    assert result.status == "success"
    assert "player_visible" in result.matched


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


def test_external_probe_pass() -> None:
    contract = _contract([
        SuccessCriterion(name="probe", description="", verification="external_probe", probe_id="test_probe"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        goal_probes={"test_probe": lambda: True},
    )
    assert result.status == "success"


def test_external_probe_fail() -> None:
    contract = _contract([
        SuccessCriterion(name="probe", description="", verification="external_probe", probe_id="test_probe"),
    ])
    result = evaluate_finish_claim(
        contract=contract,
        goal_probes={"test_probe": lambda: False},
    )
    assert result.status == "failure"


def test_external_probe_not_registered() -> None:
    contract = _contract([
        SuccessCriterion(name="probe", description="", verification="external_probe", probe_id="missing"),
    ])
    result = evaluate_finish_claim(contract=contract, goal_probes={})
    assert result.status == "failure"


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
