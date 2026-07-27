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


def test_goal_agenda_precedes_screen_belief_and_ledger_movement_advances() -> None:
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
    }

    block, _metrics = build_plan_context_block(state)
    liveness = context_module.trajectory_liveness(
        tried_actions=[],
        visited_states=[],
        criterion_history=history,
        budget={"novelty_exhaustion_steps": 4},
    )

    assert block.index("goal_agenda") < block.index("screen_belief")
    assert "已满足: 打开小红书(app.foreground_identity)" in block
    assert "未满足: 查看银石赛道相关内容(vlm_judge, 待验收)" in block
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
