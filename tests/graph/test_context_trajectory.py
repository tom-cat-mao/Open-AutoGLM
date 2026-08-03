"""Trajectory-level loop detection and context budget regression tests.

All fixtures come from the 小红书 "Chaster 117" run, where the agent tapped the same
note card at steps 4, 6 and 11. Every tap genuinely opened the detail page, so the
per-step verdict was `succeeded` each time and nothing in the graph noticed the loop:

* `detect_repeated_failure` short-circuits on non-failed outcomes, so an
  all-succeeded loop never entered the repeat counter.
* `tried_actions` recorded the path correctly but was truncated away from step 6
  onward, because new entries append at the tail while `trim_text` cuts the tail.

The stable identity across the three taps was the target geometry
(center [747, 418] / bbox [506, 214, 988, 622]) plus the surface. Both `screen_id`
namespaces changed after the intervening Back, so neither can key the dedup.
"""

from __future__ import annotations

import json

import phone_agent.graph.context as context_module

from phone_agent.graph.context import (
    build_plan_context_block,
    detect_repeated_action,
    detect_repeated_failure,
    update_gui_memory,
)
from phone_agent.graph.goal_evidence import criterion_history_from_ledger

FEED_SURFACE = "com.xingin.xhs/com.xingin.alioth.search.GlobalSearchActivity"
NOTE_CARD_CENTER = [747.0, 418.0]


def _trajectory_step(index: int, *, surface: str, screen_id: str) -> dict[str, object]:
    return {
        "step_count": index,
        "surface": surface,
        "screen_id": screen_id,
        "reflection_verdict": "partial" if index % 2 else "succeeded",
    }


def test_long_novel_trajectory_remains_exploring() -> None:
    steps = [
        _trajectory_step(index, surface=f"surface-{index}", screen_id=f"screen-{index}")
        for index in range(1, 17)
    ]
    criteria = [
        {"observation_epoch": index, "per_criterion": {"target": "unknown"}}
        for index in range(1, 17)
    ]

    for length in range(2, len(steps) + 1):
        result = context_module.trajectory_liveness(
            tried_actions=steps[:length],
            visited_states=[],
            criterion_history=criteria[:length],
            budget={"novelty_exhaustion_steps": 4},
        )
        assert result["state"] == "exploring"


def test_successful_two_state_oscillation_becomes_stuck() -> None:
    steps = [
        _trajectory_step(
            index,
            surface=f"surface-{index % 2}",
            screen_id=f"screen-{index % 2}",
        )
        for index in range(1, 9)
    ]
    criteria = [
        {"observation_epoch": index, "per_criterion": {"target": "unknown"}}
        for index in range(1, 9)
    ]

    result = context_module.trajectory_liveness(
        tried_actions=steps,
        visited_states=[],
        criterion_history=criteria,
        budget={"novelty_exhaustion_steps": 4},
    )

    assert result["state"] == "stuck"
    assert result["novelty_streak"] >= 4


def test_semantic_two_state_oscillation_is_stuck_despite_unique_screen_hashes() -> None:
    visited = [
        {
            "surface": "ProfileActivity",
            "screen_id": f"pixel-hash-{index}",
            "semantic_screen_id": f"semantic-{index % 2}",
        }
        for index in range(8)
    ]

    result = context_module.trajectory_liveness(
        tried_actions=[],
        visited_states=visited,
        criterion_history=[],
        budget={"novelty_exhaustion_steps": 4},
    )

    assert result["state"] == "stuck"
    assert result["novelty_streak"] >= 4


def test_update_gui_memory_oscillation_reaches_stuck_via_transition_stream() -> None:
    """End-to-end: deduped visited_screens compresses A<->B to two entries, so
    liveness must read the raw transition stream or stuck stays unreachable."""

    memory: dict = {}
    for index in range(8):
        state = {
            "step_count": index,
            "gui_memory": memory,
            "observation": {"snapshot": {"foreground_activity": "ProfileActivity"}},
        }
        memory = update_gui_memory(
            state,
            current_app="小红书",
            screen_id=f"pixel-hash-{index}",
            reached_surface="ProfileActivity",
            semantic_screen_id=f"semantic-{index % 2}",
        )

    # Display memory dedupes consecutive identical identities; the oscillation
    # keeps alternating, so only adjacent duplicates are dropped. The raw
    # stream keeps every transition.
    assert len(memory["visited_screens"]) == 8
    assert len(memory["screen_transition_stream"]) == 8

    stream = [
        {**item, "_transition_stream": True}
        for item in memory["screen_transition_stream"]
    ]
    result = context_module.trajectory_liveness(
        tried_actions=[],
        visited_states=stream,
        criterion_history=[],
        budget={"novelty_exhaustion_steps": 4},
    )

    assert result["state"] == "stuck"
    assert result["novelty_streak"] >= 4


def test_update_gui_memory_fresh_surfaces_keep_exploring_via_transition_stream() -> None:
    memory: dict = {}
    for index in range(8):
        state = {
            "step_count": index,
            "gui_memory": memory,
            "observation": {"snapshot": {"foreground_activity": f"Activity-{index}"}},
        }
        memory = update_gui_memory(
            state,
            current_app="小红书",
            screen_id=f"pixel-hash-{index}",
            reached_surface=f"Activity-{index}",
            semantic_screen_id=f"semantic-{index}",
        )

    stream = [
        {**item, "_transition_stream": True}
        for item in memory["screen_transition_stream"]
    ]
    result = context_module.trajectory_liveness(
        tried_actions=[],
        visited_states=stream,
        criterion_history=[],
        budget={"novelty_exhaustion_steps": 4},
    )

    assert result["state"] == "exploring"
    assert result["novelty_streak"] == 0


def test_criterion_movement_is_advancing_and_resets_novelty() -> None:
    result = context_module.trajectory_liveness(
        tried_actions=[
            _trajectory_step(index, surface="same", screen_id=f"screen-{index % 2}")
            for index in range(1, 9)
        ],
        visited_states=[],
        criterion_history=[
            {"per_criterion": {"target": "unknown"}},
            {"per_criterion": {"target": "matched"}},
        ],
        budget={"novelty_exhaustion_steps": 4},
    )

    assert result == {
        "state": "advancing",
        "reasons": ["criterion_movement"],
        "novelty_streak": 0,
    }


def test_goal_agenda_precedes_other_sections_and_ledger_movement_advances() -> None:
    ledger = [
        {
            "contract_id": "contract-1",
            "criterion_id": "app_open",
            "status": "unknown",
            "screen_id": "screen-1",
            "observation_epoch": 1,
        },
        {
            "contract_id": "contract-1",
            "criterion_id": "app_open",
            "status": "matched",
            "screen_id": "screen-2",
            "observation_epoch": 2,
        },
    ]
    history = criterion_history_from_ledger(ledger, contract_id="contract-1")
    state = {
        "goal_agenda": [
            {
                "description": "打开小红书",
                "status": "satisfied",
                "verification": "app_or_activity_match",
                "predicate_id": "app.foreground_identity",
            },
            {
                "description": "查看银石赛道相关内容",
                "status": "unknown",
                "verification": "vlm_judge",
                "predicate_id": None,
            },
        ],
        "screen_belief": {"summary": "搜索结果页", "confidence": "high"},
        "failure_memory": [{"failure_cause": "wrong_page", "action": "Tap"}],
    }

    block, _metrics = build_plan_context_block(state)
    liveness = context_module.trajectory_liveness(
        tried_actions=[],
        visited_states=[],
        criterion_history=history,
        budget={"novelty_exhaustion_steps": 4},
    )

    # 1.4: screen_belief is no longer rendered in the plan block at all, so
    # goal_agenda is the first rendered section.
    assert block.index("goal_agenda") < block.index("failure_memory")
    assert "已满足: 打开小红书(app.foreground_identity)" in block
    assert "未满足: 查看银石赛道相关内容(vlm_judge, 待验收)" in block
    assert "screen_belief" not in block
    assert history
    assert liveness["state"] == "advancing"


def _succeeded_tap(step: int, *, screen_id: str) -> dict[str, object]:
    """One of the repeated taps, exactly as reflect recorded it: fully successful."""

    return {
        "step_count": step,
        "screen_id": screen_id,
        "action": "Tap",
        "mark_id": "ax_18",
        "target_center": NOTE_CARD_CENTER,
        "surface": FEED_SURFACE,
        "result_success": True,
        "failure_cause": None,
        "reflection_verdict": "succeeded",
        "execution_success": True,
    }


def test_repeated_action_detected_although_every_step_succeeded() -> None:
    # The two screen_ids differ, as they did in the real run after the Back.
    history = [
        _succeeded_tap(4, screen_id="6f5b7ab63c2ba295"),
        _succeeded_tap(6, screen_id="6e9216c8c675a04b"),
    ]
    outcome = _succeeded_tap(11, screen_id="6e9216c8c675a04b")

    assert detect_repeated_action(history, outcome) is True


def test_repeated_failure_stays_blind_to_a_successful_loop() -> None:
    """Pins the division of labour: failure memory is not a loop detector."""

    history = [_succeeded_tap(4, screen_id="6f5b7ab63c2ba295")]
    outcome = _succeeded_tap(6, screen_id="6e9216c8c675a04b")

    assert detect_repeated_failure(history, outcome) is False


def test_distinct_targets_on_one_surface_are_not_a_repeat() -> None:
    """Steps 9 and 10 tapped different tabs on one surface; that is progress."""

    user_tab = {**_succeeded_tap(9, screen_id="s1"), "target_center": [260.0, 134.0]}
    all_tab = {**_succeeded_tap(10, screen_id="s1"), "target_center": [78.0, 134.0]}

    assert detect_repeated_action([user_tab], all_tab) is False


def test_gui_memory_records_target_geometry_for_dedup() -> None:
    state = {
        "step_count": 6,
        "action_parsed": {
            "_metadata": "do",
            "action": "Tap",
            "element": NOTE_CARD_CENTER,
        },
        "intent_raw": {"target_mark_id": "ax_18"},
        "grounding_observation": {"center": NOTE_CARD_CENTER, "target": {"mark_id": "ax_18"}},
        "observation": {"snapshot": {"foreground_activity": FEED_SURFACE}},
        "action_result": {"success": True},
    }

    memory = update_gui_memory(state, current_app="小红书", screen_id="6e9216c8c675a04b")

    latest = memory["tried_actions"][-1]
    assert latest["target_center"] == NOTE_CARD_CENTER
    assert latest["surface"] == FEED_SURFACE


def test_gui_memory_aligns_visited_surface_with_reached_screen() -> None:
    state = {
        "step_count": 6,
        "action_parsed": {
            "_metadata": "do",
            "action": "Tap",
            "element": NOTE_CARD_CENTER,
        },
        "observation": {"snapshot": {"foreground_activity": FEED_SURFACE}},
    }

    memory = update_gui_memory(
        state,
        current_app="小红书",
        screen_id="detail-screen",
        reached_surface="NoteDetailActivity",
    )

    assert memory["visited_screens"][-1]["surface"] == "NoteDetailActivity"
    assert memory["tried_actions"][-1]["surface"] == FEED_SURFACE


def test_newest_tried_actions_survive_the_context_budget() -> None:
    """Step 6 lost `tried_actions` entirely; the newest entries must never be first out."""

    tried = [_succeeded_tap(step, screen_id=f"screen{step}") for step in range(1, 12)]
    state = {
        "step_count": 11,
        # A long history is what crowded gui_memory out of the block in the real run.
        "summarized_history": "\n".join(
            f"step={step} action=Tap target=ax_18 success=True verdict=succeeded"
            for step in range(1, 12)
        ),
        "gui_memory": {
            "visited_screens": [
                {"screen_id": f"screen{step}", "current_app": "小红书", "step_count": step}
                for step in range(1, 12)
            ],
            "tried_actions": tried,
            "scroll_memory": {},
            "task_progress": {"last_verdict": "succeeded"},
        },
    }

    block, _metrics = build_plan_context_block(state)

    assert "ax_18" in block
    # The newest entry (step 11) is the one a loop check needs and the one tail
    # truncation used to remove first.
    assert "s11 " in block


def test_summarized_history_line_names_the_target() -> None:
    """A history of bare `action=Tap` lines cannot separate progress from a loop."""

    from phone_agent.graph.context import (
        build_action_outcome_summary,
        update_summarized_history,
    )

    outcome = build_action_outcome_summary(
        {
            "step_count": 6,
            "action_parsed": {"_metadata": "do", "action": "Tap"},
            "intent_raw": {"target_mark_id": "ax_18"},
            "action_result": {"success": True},
            "reflection_verdict": "succeeded",
        }
    )
    history, _truncated = update_summarized_history("", outcome)

    assert "target=ax_18" in history


def test_repeat_warning_reaches_the_injected_block() -> None:
    """The prompt already documents `avoid_repeating`; it must have a real writer."""

    state = {
        "step_count": 11,
        "repeated_action_detected": True,
        "gui_memory": {
            "visited_screens": [],
            "tried_actions": [
                _succeeded_tap(4, screen_id="a"),
                _succeeded_tap(6, screen_id="b"),
                _succeeded_tap(11, screen_id="b"),
            ],
            "scroll_memory": {},
            "task_progress": {},
        },
    }

    block, metrics = build_plan_context_block(state)

    assert "avoid_repeating" in block
    assert "repeated_action" in json.dumps(metrics, ensure_ascii=False) or "avoid_repeating" in block


def test_failure_memory_renders_up_to_budget_with_repeat_count() -> None:
    """1.3: failure_memory renders the full budget (3), not just the last item,
    and carries the repeated_failure_count counter line."""
    state = {
        "failure_memory": [
            {"step_count": 1, "action": "Tap", "failure_cause": "wrong_page"},
            {"step_count": 2, "action": "Tap", "failure_cause": "wrong_page"},
            {"step_count": 3, "action": "Type", "failure_cause": "element_not_found"},
            {"step_count": 4, "action": "Tap", "failure_cause": "wrong_page"},
        ],
        "repeated_failure_count": 7,
    }

    block, _metrics = build_plan_context_block(state)

    assert "failure_memory" in block
    # The oldest entry (step 1) is dropped; the newest three survive.
    assert "\"step_count\": 1" not in block
    assert "\"step_count\": 2" in block
    assert "\"step_count\": 4" in block
    assert "\"repeated_failure_count\": 7" in block


def test_liveness_note_heads_the_plan_context_block() -> None:
    """2.2: liveness state is a natural-language hint at the top of the block,
    carrying novelty streak and repeat counts, in both languages."""
    tried = [
        _succeeded_tap(4, screen_id="a"),
        _succeeded_tap(6, screen_id="b"),
        _succeeded_tap(7, screen_id="b"),
        _succeeded_tap(8, screen_id="b"),
    ]
    state = {
        "lang": "cn",
        "gui_memory": {
            "visited_screens": [],
            "tried_actions": tried,
            "scroll_memory": {},
            "task_progress": {
                "trajectory_liveness": "stuck",
                "novelty_streak": 3,
                "stuck_rounds": 2,
            },
        },
        "failure_memory": [],
    }

    block, _metrics = build_plan_context_block(state)
    assert block.index("轨迹提示") < block.index("gui_memory")
    assert "陷入循环" in block
    assert "novelty_streak=3" in block
    assert "stuck_rounds=2" in block
    assert "已重复 4 次" in block

    en_block, _ = build_plan_context_block({**state, "lang": "en"})
    assert "Trajectory note: stuck" in en_block
    assert "repeated 4x" in en_block


def test_liveness_note_absent_without_liveness_state() -> None:
    block, _metrics = build_plan_context_block(
        {"gui_memory": {"tried_actions": [], "task_progress": {}}}
    )
    assert "轨迹提示" not in block
    assert "liveness_note" not in block


def test_verifier_advisory_renders_in_last_action_outcome() -> None:
    """1.1: verifier signals ride into the next plan prompt as advisory
    evidence inside last_action_outcome."""
    state = {
        "action_outcome_summary": {
            "action": "Tap",
            "execution_success": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
            "verifier_advisory": {
                "status": "unknown",
                "confidence": 0.0,
                "failure_cause": None,
                "matched_postconditions": [],
                "missing_postconditions": ["postcondition_unverified"],
                "selected_object_signals": {},
            },
        },
        "reflection_verdict": "succeeded",
        "failure_cause": None,
        "suggested_strategy": "continue",
    }

    block, _metrics = build_plan_context_block(state)

    assert "last_action_outcome" in block
    assert "verifier_advisory" in block
    assert "postcondition_unverified" in block


def test_verifier_advisory_sensitive_postcondition_is_redacted() -> None:
    """Advisory postconditions can echo raw screen text: regex redaction must
    apply before the block is emitted."""
    state = {
        "action_outcome_summary": {
            "action": "Type",
            "execution_success": True,
            "reflection_verdict": "succeeded",
            "failure_cause": None,
            "verifier_advisory": {
                "status": "success",
                "confidence": 0.9,
                "failure_cause": None,
                "matched_postconditions": ["订单 order:ABCD1234", "13800138000"],
                "missing_postconditions": [],
                "selected_object_signals": {},
            },
        },
        "reflection_verdict": "succeeded",
        "failure_cause": None,
        "suggested_strategy": "continue",
    }

    block, _metrics = build_plan_context_block(state)

    assert "13800138000" not in block
    assert "verifier_advisory" in block


def _swipe_entry(
    start: list[float], end: list[float], *, surface: str = FEED_SURFACE
) -> dict[str, object]:
    return {
        "action": "Swipe",
        "start": start,
        "end": end,
        "target_center": None,
        "surface": surface,
    }


def test_swipe_repeat_key_uses_grid_and_direction() -> None:
    """P3 #3: a Swipe without a target center keys on (action, surface,
    direction, start-grid, end-grid); sub-50px jitter lands in the same grid."""
    base = _swipe_entry([500, 900], [500, 300])
    jittered = _swipe_entry([505, 895], [498, 302])
    other_start = _swipe_entry([800, 900], [800, 300])
    other_surface = _swipe_entry([500, 900], [500, 300], surface="other-surface")

    assert context_module.repeated_action_key(base) is not None
    assert context_module.repeated_action_key(base) == context_module.repeated_action_key(
        jittered
    )
    assert context_module.repeated_action_key(base) != context_module.repeated_action_key(
        other_start
    )
    assert context_module.repeated_action_key(base) != context_module.repeated_action_key(
        other_surface
    )


def test_swipe_missing_geometry_has_no_repeat_key() -> None:
    assert context_module.repeated_action_key({"action": "Swipe", "surface": FEED_SURFACE}) is None
    assert context_module.repeated_action_key({"action": "Tap", "surface": FEED_SURFACE}) is None


def test_detect_repeated_action_counts_swipe_gestures() -> None:
    """Same swipe twice on the same surface reaches the repeat threshold; a
    different start point is progress, not a repeat."""
    history = [
        _swipe_entry([500, 900], [500, 300]),
        _swipe_entry([500, 900], [500, 300]),
    ]
    candidate = _swipe_entry([505, 895], [498, 302])

    assert detect_repeated_action(history, candidate) is True
    assert detect_repeated_action(history[:-1], _swipe_entry([800, 900], [800, 300])) is False


def test_gui_memory_records_swipe_geometry_for_dedup() -> None:
    state = {
        "step_count": 6,
        "action_parsed": {
            "_metadata": "do",
            "action": "Swipe",
            "start": [500, 900],
            "end": [500, 300],
        },
        "observation": {"snapshot": {"foreground_activity": FEED_SURFACE}},
        "action_result": {"success": True},
    }

    memory = update_gui_memory(state, current_app="小红书", screen_id="screen-1")

    latest = memory["tried_actions"][-1]
    assert latest["action"] == "Swipe"
    assert latest["target_center"] is None
    assert latest["start"] == [500.0, 900.0]
    assert latest["end"] == [500.0, 300.0]
    assert context_module.repeated_action_key(latest) is not None


def test_failure_memory_write_mode_matrix() -> None:
    """P3 #2 matrix: hard_failure/consensus → verified; model-alone → unverified;
    disputed and non-failure → skip."""
    cases = [
        # (verifier_status, verdict, hard_failure, disputed, expected)
        ("failure", "failed", False, False, "verified"),
        ("failure", "failed", True, False, "verified"),
        ("unknown", "failed", False, False, "unverified"),
        ("blocked", "partial", False, False, "unverified"),
        ("success", "failed", False, False, "unverified"),
        ("success", "failed", False, True, "skip"),
        ("failure", "failed", False, True, "skip"),
        ("unknown", "succeeded", False, False, "skip"),
        ("failure", "succeeded", False, False, "skip"),
    ]
    for verifier_status, verdict, hard_failure, disputed, expected in cases:
        assert (
            context_module.failure_memory_write_mode(
                verifier_status=verifier_status,
                verdict=verdict,
                hard_failure=hard_failure,
                disputed=disputed,
            )
            == expected
        ), (verifier_status, verdict, hard_failure, disputed)


def test_update_failure_memory_marks_unverified_only_when_flagged() -> None:
    outcome = {
        "step_count": 7,
        "action": "Tap",
        "current_app": "小红书",
        "failure_cause": "element_not_found",
        "reflection_verdict": "failed",
        "suggested_strategy": "retry",
    }

    verified = context_module.update_failure_memory([], outcome, unverified=False)
    assert "unverified" not in verified[0]

    unverified = context_module.update_failure_memory([], outcome, unverified=True)
    assert unverified[0]["unverified"] is True
    assert unverified[0]["failure_cause"] == "element_not_found"
