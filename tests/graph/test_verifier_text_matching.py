from phone_agent.graph.verifier import (
    VerifierResult,
    _match_expected_text,
    _normalize_surface_identity,
    _selected_object_signals,
    merge_verifier_with_reflection,
    verify_action_outcome,
)
from phone_agent.graph.expected_outcome import default_expected_outcome
from phone_agent.graph.nodes.reflect import _sanitize_verifier_result_dict

# Regression fixtures from the 小红书 "Chaster 117" run: the tapped note card was a
# bare layout container with no text of its own, so its `text_summary` fell back to
# the Java class name. Every screen contains such a container, which made the
# `evidence_summary in text_blob` check a tautology.
CLASS_NAME_ONLY_EVIDENCE = "FrameLayout"
FEED_SURFACE = "com.xingin.xhs/com.xingin.alioth.search.GlobalSearchActivity"
DETAIL_SURFACE = "com.xingin.xhs/com.xingin.matrix.notedetail.NoteDetailActivity"


def test_selected_object_success_vs_model_failed_becomes_disputed() -> None:
    """P3 #1b: verifier success with matched postconditions vs a model failed
    verdict is a conflict — neither wins; the step becomes partial/unknown with
    disputed=True, and the verifier evidence rides along as advisory."""
    verifier = VerifierResult(
        status="success",
        confidence=0.75,
        evidence={
            "matched_postconditions": ["selected_object_match"],
            "selected_object_signals": {
                "selected_object_text_match": True,
                "selected_object_surface_changed": True,
            },
        },
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": "wrong_page",
        },
    )

    assert merged["action_succeeded"] is False
    assert merged["reflection_verdict"] == "partial"
    assert merged["failure_cause"] == "unknown"
    assert merged["disputed"] is True
    advisory = merged["verifier_advisory"]
    assert advisory["status"] == "success"
    assert advisory["confidence"] == 0.75
    assert advisory["matched_postconditions"] == ["selected_object_match"]
    assert advisory["selected_object_signals"]["selected_object_text_match"] is True


def test_verifier_high_confidence_success_vs_model_failed_is_disputed() -> None:
    """P3: a conf>=0.9 verifier success still does not override the model — but
    with matched postconditions present it is a conflict (disputed), not a
    straight model win."""
    verifier = VerifierResult(
        status="success",
        confidence=0.9,
        evidence={"matched_postconditions": ["input_focused"]},
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": "element_not_found",
        },
    )

    assert merged["reflection_verdict"] == "partial"
    assert merged["disputed"] is True
    assert merged["verifier_advisory"]["confidence"] == 0.9


def test_verifier_success_without_matched_postconditions_stays_advisory() -> None:
    """P3: verifier success without matched postconditions carries no positive
    evidence, so a model failure is not disputed — the model verdict passes
    through and the advisory rides along."""
    verifier = VerifierResult(
        status="success", confidence=0.9, evidence={"matched_postconditions": []}
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": "unknown",
        },
    )

    assert merged["reflection_verdict"] == "failed"
    assert merged["disputed"] is False
    assert merged["verifier_advisory"]["status"] == "success"


def test_verifier_failure_is_advisory_not_model_override() -> None:
    """The conf>=0.7 failure branch is gone: only hard_failure overrides."""
    verifier = VerifierResult(
        status="failure",
        confidence=0.75,
        failure_cause="wrong_page",
        evidence={"missing_postconditions": ["selected_object_detail_not_opened"]},
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
        },
    )

    assert merged["reflection_verdict"] == "succeeded"
    assert merged["verifier_advisory"]["missing_postconditions"] == [
        "selected_object_detail_not_opened"
    ]


def test_verifier_unknown_missing_postconditions_do_not_override_success() -> None:
    """The unknown+missing->failed override is gone: absence stays advisory."""
    verifier = VerifierResult(
        status="unknown",
        confidence=0.0,
        evidence={"missing_postconditions": ["after_observation_unavailable"]},
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
            "reflection_has_evidence": False,
        },
    )

    assert merged["reflection_verdict"] == "succeeded"
    assert merged["verifier_advisory"]["status"] == "unknown"


def test_hard_failure_still_overrides_model_success() -> None:
    """The single remaining override: deterministic execution failure."""
    verifier = VerifierResult(
        status="failure",
        confidence=0.9,
        hard_failure=True,
        failure_cause="app_not_responding",
        evidence={"result_message_summary": "Action failed: boom"},
    )

    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
        },
    )

    assert merged["action_succeeded"] is False
    assert merged["reflection_verdict"] == "failed"
    assert merged["failure_cause"] == "app_not_responding"
    assert merged["verifier_advisory"]["status"] == "failure"


def test_back_default_outcome_verifies_surface_change_programmatically() -> None:
    outcome = default_expected_outcome(action={"_metadata": "do", "action": "Back"})

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Back"},
            "expected_outcome": outcome.to_dict(),
            "current_app": "小红书",
        },
        after_screenshot=None,
        after_app="小红书",
        action_result={"success": True},
        before_observation={
            "snapshot": {"foreground_activity": FEED_SURFACE, "screen_id": "feed"}
        },
        after_observation={
            "snapshot": {"foreground_activity": DETAIL_SURFACE, "screen_id": "detail"}
        },
    )

    assert outcome.kind == "surface_changed"
    assert result.status == "success"
    assert result.evidence["matched_postconditions"] == ["surface_changed"]


# The two observation payloads are built by different producers and disagree on
# shape: `state_before_observation_payload` exposes a bare activity class, while
# the after payload carries `device_signals.top_activity` as `package/activity`.
# The 限速摩卡 run compared those raw strings, so one physical screen always
# looked like a surface change.
BARE_PROFILE_ACTIVITY = "com.xingin.matrix.v2.profile.newpage.NewOtherUserActivity"
BARE_SEARCH_ACTIVITY = "com.xingin.alioth.search.GlobalSearchActivity"
XHS_PACKAGE = "com.xingin.xhs"


def _before_payload(activity: str, *, screen_id: str = "screen") -> dict:
    """Before observation: snapshot only, no device_signals."""

    return {
        "snapshot": {
            "screen_id": screen_id,
            "foreground_activity": activity,
            "current_app": "小红书",
        },
        "marks": [],
    }


def _after_payload(activity: str, *, screen_id: str = "screen") -> dict:
    """After observation: snapshot plus component-shaped device signals."""

    component = f"{XHS_PACKAGE}/{activity}"
    return {
        "snapshot": {
            "screen_id": screen_id,
            "foreground_activity": activity,
            "current_app": "小红书",
        },
        "marks": [{"mark_id": "ax_21", "role": "TextView", "text_summary": "搜索"}],
        "device_signals": {
            "top_activity": component,
            "focused_window": component,
        },
    }


def test_surface_identity_normalizes_component_and_bare_activity_alike() -> None:
    assert (
        _normalize_surface_identity(f"{XHS_PACKAGE}/{BARE_PROFILE_ACTIVITY}")
        == BARE_PROFILE_ACTIVITY
    )
    assert _normalize_surface_identity(BARE_PROFILE_ACTIVITY) == BARE_PROFILE_ACTIVITY
    # Android shorthand and degenerate inputs.
    assert _normalize_surface_identity("com.pkg/.Inner") == "com.pkg.Inner"
    assert _normalize_surface_identity("com.pkg/") == "com.pkg"
    assert _normalize_surface_identity(None) == ""
    assert _normalize_surface_identity("  ") == ""


def test_same_physical_screen_is_not_reported_as_a_surface_change() -> None:
    signals = _selected_object_signals(
        {
            "object_type": "input",
            "evidence_summary": "搜索",
            "expected_page_type": "page_opened",
        },
        _after_payload(BARE_PROFILE_ACTIVITY),
        "搜索",
        None,
        before_observation=_before_payload(BARE_PROFILE_ACTIVITY),
    )

    assert "page_changed_object_check_skipped" not in signals
    assert signals["selected_object_surface_changed"] is False
    assert signals["same_surface_still_visible"] is True


def test_real_navigation_degrades_selected_object_check() -> None:
    """P3 #4: when the mark set is rebuilt across pages (here signalled by
    activity migration), selected-object comparison is skipped entirely instead
    of claiming a surface change — before-page mark bindings no longer hold on
    the after page, so the group must not feed success/failure judgement."""
    signals = _selected_object_signals(
        {
            "object_type": "input",
            "evidence_summary": "搜索",
            "expected_page_type": "page_opened",
        },
        _after_payload(BARE_SEARCH_ACTIVITY),
        "搜索",
        None,
        before_observation=_before_payload(BARE_PROFILE_ACTIVITY),
    )

    assert signals.get("page_changed_object_check_skipped") is True
    assert "selected_object_surface_changed" not in signals
    assert "selected_object_match" not in signals
    assert "same_surface_still_visible" not in signals


def test_back_that_changed_nothing_fails_across_asymmetric_payloads() -> None:
    """A Back that did not navigate must not match `surface_changed`.

    Screen ids are held equal here so the assertion isolates surface identity.
    The `surface_changed` branch also treats a differing content-derived
    `screen_id` as proof of navigation, which is a separate open question:
    `screen_id` moves on any feed re-render, so a no-op Back on a live screen
    can still match through that path.
    """

    outcome = default_expected_outcome(action={"_metadata": "do", "action": "Back"})

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Back"},
            "expected_outcome": outcome.to_dict(),
            "current_app": "小红书",
        },
        after_screenshot=None,
        after_app="小红书",
        action_result={"success": True},
        before_observation=_before_payload(BARE_PROFILE_ACTIVITY, screen_id="same"),
        after_observation=_after_payload(BARE_PROFILE_ACTIVITY, screen_id="same"),
    )

    assert result.status == "failure"
    assert result.failure_cause == "wrong_page"
    assert result.evidence["missing_postconditions"] == ["surface_changed"]


def test_reflect_prompt_keeps_postcondition_text_while_trace_projection_redacts() -> None:
    verifier = VerifierResult(
        status="success",
        confidence=0.9,
        evidence={"matched_postconditions": ["订单 order:ABCD1234"]},
    )

    prompt = _sanitize_verifier_result_dict(verifier, consumer="reflect_prompt")
    trace = _sanitize_verifier_result_dict(verifier)

    assert prompt["evidence"]["matched_postconditions"] == ["订单 order:ABCD1234"]
    assert trace["evidence"]["matched_postconditions"][0]["redacted"] is True


def test_expected_text_contains_is_case_insensitive_for_uppercase_input() -> None:
    matched, missing = _match_expected_text(["VillageThomas"], "result: villagethomas")

    assert matched == ["VillageThomas"]
    assert missing == []


def test_sensitive_expected_text_verifies_with_raw_contains() -> None:
    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Wait"},
            "expected_outcome": {
                "kind": "target_appeared",
                "must_observe": ["13800138000"],
            },
        },
        after_screenshot=object(),
        after_app="Settings",
        action_result={"success": True, "message": "ok"},
        after_observation={"marks": [{"text_summary": "contact 13800138000"}]},
    )

    assert result.status == "success"
    assert result.evidence["matched_postconditions"] == ["13800138000"]


def test_class_name_evidence_is_not_a_match() -> None:
    """A class-name-shaped evidence summary carries no content, so it cannot match.

    Every screen renders anonymous layout containers, so accepting a class name as
    evidence makes the containment check true regardless of what was tapped.
    """

    signals = _selected_object_signals(
        {
            "object_type": "control",
            "evidence_summary": CLASS_NAME_ONLY_EVIDENCE,
            "expected_page_type": "page_opened",
        },
        {"marks": [{"text_summary": CLASS_NAME_ONLY_EVIDENCE}]},
        CLASS_NAME_ONLY_EVIDENCE.casefold(),
        None,
    )

    assert signals["selected_object_text_match"] is False
    assert "selected_object_match" not in signals


def test_textless_target_does_not_reach_high_confidence_success() -> None:
    """A text-less target must not become conclusive selected-object evidence."""

    result = verify_action_outcome(
        before_state={
            "action_parsed": {"_metadata": "do", "action": "Tap", "element": [747, 418]},
            "expected_outcome": {
                "kind": "target_appeared",
                "object_type": "control",
                "evidence_summary": CLASS_NAME_ONLY_EVIDENCE,
                "expected_page_type": "page_opened",
            },
        },
        after_screenshot=object(),
        after_app="小红书",
        action_result={"success": True, "message": "ok"},
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
        after_observation={
            "marks": [{"text_summary": CLASS_NAME_ONLY_EVIDENCE}],
            "device_signals": {"top_activity": DETAIL_SURFACE},
        },
    )

    assert result.confidence < 0.9


def test_same_surface_after_tap_is_reported() -> None:
    """Tapping without leaving the surface is the 'detail never opened' case."""

    signals = _selected_object_signals(
        {
            "object_type": "card",
            "evidence_summary": "26年F1超车真的没意义了么？",
            "expected_page_type": "page_opened",
        },
        {
            "snapshot": {"foreground_activity": FEED_SURFACE},
            "device_signals": {"top_activity": FEED_SURFACE},
            "marks": [{"text_summary": "26年F1超车真的没意义了么？"}],
        },
        "26年f1超车真的没意义了么？",
        None,
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
    )

    assert signals["same_surface_still_visible"] is True


def test_surface_change_clears_same_surface_signal() -> None:
    """Opening the detail surface must not report 'still on the same surface'."""

    signals = _selected_object_signals(
        {
            "object_type": "card",
            "evidence_summary": "26年F1超车真的没意义了么？",
            "expected_page_type": "page_opened",
        },
        {
            "snapshot": {"foreground_activity": DETAIL_SURFACE},
            "device_signals": {"top_activity": DETAIL_SURFACE},
            "marks": [{"text_summary": "26年F1超车真的没意义了么？"}],
        },
        "26年f1超车真的没意义了么？",
        None,
        before_observation={"snapshot": {"foreground_activity": FEED_SURFACE}},
    )

    assert not signals.get("same_surface_still_visible")


def test_arbitration_matrix() -> None:
    """P3 #1 four-combination matrix for verifier-vs-model arbitration."""
    success_verifier = VerifierResult(
        status="success",
        confidence=0.9,
        evidence={"matched_postconditions": ["app_opened"]},
    )
    failure_verifier = VerifierResult(
        status="failure",
        confidence=0.75,
        failure_cause="wrong_page",
        evidence={"missing_postconditions": ["app_opened"]},
    )
    unknown_verifier = VerifierResult(
        status="unknown", confidence=0.0, evidence={"matched_postconditions": []}
    )

    # a. hard_failure overrides model success.
    hard = merge_verifier_with_reflection(
        VerifierResult(
            status="failure", hard_failure=True, failure_cause="app_not_responding"
        ),
        {"action_succeeded": True, "reflection_verdict": "succeeded"},
    )
    assert hard["reflection_verdict"] == "failed"
    assert hard["disputed"] is False

    # b. verifier success + matched vs model failed → disputed partial.
    disputed = merge_verifier_with_reflection(
        success_verifier,
        {"action_succeeded": False, "reflection_verdict": "failed", "failure_cause": "wrong_page"},
    )
    assert disputed["reflection_verdict"] == "partial"
    assert disputed["failure_cause"] == "unknown"
    assert disputed["disputed"] is True

    # c. consensus failure (verifier failure + model failed) keeps failure.
    consensus = merge_verifier_with_reflection(
        failure_verifier,
        {"action_succeeded": False, "reflection_verdict": "failed", "failure_cause": "wrong_page"},
    )
    assert consensus["reflection_verdict"] == "failed"
    assert consensus["disputed"] is False

    # d. pass-through: verifier unknown + model failed → model keeps the verdict.
    passthrough = merge_verifier_with_reflection(
        unknown_verifier,
        {"action_succeeded": False, "reflection_verdict": "failed", "failure_cause": "unknown"},
    )
    assert passthrough["reflection_verdict"] == "failed"
    assert passthrough["disputed"] is False
    # pass-through: verifier failure + model succeeded → model keeps the verdict.
    passthrough_success = merge_verifier_with_reflection(
        failure_verifier,
        {"action_succeeded": True, "reflection_verdict": "succeeded", "failure_cause": None},
    )
    assert passthrough_success["reflection_verdict"] == "succeeded"
    assert passthrough_success["disputed"] is False


def test_wrong_page_narrow_veto_disputes_when_activity_migrated() -> None:
    """P3 #1b: model claims wrong_page but before/after observations show the
    activity migrated — the 'wrong page' judgement lacks evidence, so the step
    is disputed even when the verifier is not success."""
    verifier = VerifierResult(
        status="unknown", confidence=0.25, evidence={"missing_postconditions": []}
    )
    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": "wrong_page",
        },
        observation_before={
            "snapshot": {"foreground_activity": FEED_SURFACE, "screen_id": "feed"}
        },
        observation_after={
            "snapshot": {"foreground_activity": DETAIL_SURFACE, "screen_id": "detail"}
        },
    )

    assert merged["disputed"] is True
    assert merged["reflection_verdict"] == "partial"
    assert merged["failure_cause"] == "unknown"


def test_wrong_page_without_migration_keeps_failure() -> None:
    verifier = VerifierResult(
        status="unknown", confidence=0.25, evidence={"missing_postconditions": []}
    )
    merged = merge_verifier_with_reflection(
        verifier,
        {
            "action_succeeded": False,
            "reflection_verdict": "failed",
            "failure_cause": "wrong_page",
        },
        observation_before={
            "snapshot": {"foreground_activity": FEED_SURFACE, "screen_id": "same"}
        },
        observation_after={
            "snapshot": {"foreground_activity": FEED_SURFACE, "screen_id": "same"}
        },
    )

    assert merged["disputed"] is False
    assert merged["reflection_verdict"] == "failed"
    assert merged["failure_cause"] == "wrong_page"


def test_selected_object_degrades_when_mark_set_version_changes() -> None:
    """P3 #4: a rebuilt mark set (cross-page mark_id reassignment) skips the
    selected-object group instead of producing a false match or false failure."""
    before = {
        "snapshot": {
            "screen_id": "feed",
            "foreground_activity": FEED_SURFACE,
            "semantic_screen_id": "semantic-1",
            "mark_set_version": "version-a",
        },
        "marks": [],
    }
    after = {
        "snapshot": {
            "screen_id": "detail",
            "foreground_activity": DETAIL_SURFACE,
            "semantic_screen_id": "semantic-2",
            "mark_set_version": "version-b",
        },
        "marks": [{"mark_id": "m1", "role": "TextView", "text_summary": "视频标题一"}],
    }

    signals = _selected_object_signals(
        {
            "object_type": "video",
            "evidence_summary": "视频标题一",
            "expected_page_type": "detail_or_player",
        },
        after,
        "视频标题一",
        None,
        before_observation=before,
    )

    assert signals.get("page_changed_object_check_skipped") is True
    assert "selected_object_match" not in signals
    assert "selected_object_text_match" not in signals
    assert "same_surface_still_visible" not in signals


def test_selected_object_compares_when_mark_set_version_stable() -> None:
    """Same mark set version → same page mark binding → comparison proceeds."""
    before = {
        "snapshot": {
            "screen_id": "feed",
            "foreground_activity": FEED_SURFACE,
            "mark_set_version": "version-a",
        },
        "marks": [],
    }
    after = {
        "snapshot": {
            "screen_id": "feed-2",
            "foreground_activity": FEED_SURFACE,
            "mark_set_version": "version-a",
        },
        "marks": [{"mark_id": "m1", "role": "TextView", "text_summary": "视频标题一"}],
    }

    signals = _selected_object_signals(
        {
            "object_type": "video",
            "evidence_summary": "视频标题一",
            "expected_page_type": "detail_or_player",
        },
        after,
        "视频标题一",
        None,
        before_observation=before,
    )

    assert "page_changed_object_check_skipped" not in signals
    assert signals["selected_object_match"] is True
