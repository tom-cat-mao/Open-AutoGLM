"""Effect-guards (execution-b): the repeat guard counts CONSECUTIVE NO-EFFECT
attempts, not raw attempts; successful locates are progress (fuse 20), the
locate countdown is gone, and every tried_actions entry carries had_effect.

All tests are deterministic synthetic data — no model mocking of judgment.
"""

from __future__ import annotations

from phone_agent.config.policy import LOCATE_MAX_PER_RUN
from phone_agent.graph.context import (
    action_had_effect,
    build_plan_context_block,
    consecutive_no_effect_count,
    detect_repeated_action,
    locate_hint_digest,
    repeated_action_key,
    update_gui_memory,
)
from phone_agent.graph.marks import Mark, MarkRegistry
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.grounding.fake import FakeGroundingProvider

_SURFACE = "com.example/.MainActivity"
_SCREEN = "screen-1"


def _registry() -> MarkRegistry:
    return MarkRegistry(
        screen_id=_SCREEN,
        marks={
            "scope_full": Mark(
                mark_id="scope_full",
                screen_id=_SCREEN,
                bbox=(0, 0, 1000, 1000),
                center=(500, 500),
                source="accessibility",
                role="View",
                text_summary="全屏容器",
            )
        },
    )


def _tap(*, surface: str = _SURFACE, had_effect: bool | None = None) -> dict:
    entry = {
        "action": "Tap",
        "target_center": [500.0, 500.0],
        "surface": surface,
        "result_success": True,
        "failure_cause": None,
    }
    if had_effect is not None:
        entry["had_effect"] = had_effect
    return entry


def _tap_key(surface: str = _SURFACE):
    return repeated_action_key(_tap(surface=surface))


# ----------------------------------------------------------------------
# action_had_effect truth table
# ----------------------------------------------------------------------


def test_action_had_effect_truth_table() -> None:
    cases = [
        # (before, after, new_obs, verdict, expected)
        ("a", "a", 0, None, False),  # nothing changed
        ("a", "a", 0, "failed", False),
        ("a", "a", 0, "partial", False),  # partial never resets a streak
        ("a", "b", 0, None, True),  # screen changed
        ("a", "b", 0, "failed", True),  # screen change wins even on failure
        ("a", "a", 1, None, True),  # fresh criterion observation
        ("a", "a", 3, "failed", True),
        ("a", "a", 0, "succeeded", True),  # verified
        ("a", "a", 1, "succeeded", True),
        (None, "b", 0, None, False),  # fail-closed: unknown before = no signal
        ("a", None, 0, None, False),
        (None, None, 0, "succeeded", True),  # verdict still counts
        ("a", "a", -1, None, False),  # defensive: negative obs count
    ]
    for before, after, new_obs, verdict, expected in cases:
        assert (
            action_had_effect(
                before_screen_hash=before,
                after_screen_hash=after,
                new_observation_count=new_obs,
                verdict=verdict,
            )
            is expected
        ), (before, after, new_obs, verdict, expected)


def test_action_had_effect_defaults_are_zero_and_none() -> None:
    assert (
        action_had_effect(before_screen_hash="x", after_screen_hash="x") is False
    )
    assert action_had_effect(before_screen_hash="x", after_screen_hash="y") is True


# ----------------------------------------------------------------------
# consecutive_no_effect_count semantics
# ----------------------------------------------------------------------


def test_consecutive_no_effect_slider_shape_never_counts() -> None:
    """5 same-key attempts, every one productive → streak stays 0."""
    key = _tap_key()
    tried = [_tap(had_effect=True) for _ in range(5)]
    assert consecutive_no_effect_count(tried, key) == 0
    assert detect_repeated_action(tried[:-1], tried[-1]) is False


def test_consecutive_no_effect_true_loop_counts() -> None:
    key = _tap_key()
    tried = [_tap(had_effect=False) for _ in range(2)]
    assert consecutive_no_effect_count(tried, key) == 2
    # The third same-key no-effect attempt is exactly at the threshold.
    assert detect_repeated_action(tried, _tap(had_effect=False)) is True


def test_consecutive_no_effect_one_product_hit_resets_streak() -> None:
    key = _tap_key()
    tried = [_tap(had_effect=False), _tap(had_effect=True), _tap(had_effect=False)]
    assert consecutive_no_effect_count(tried, key) == 1
    assert detect_repeated_action(tried, _tap(had_effect=False)) is False


def test_consecutive_no_effect_interleaved_other_keys_do_not_reset() -> None:
    """Another target's attempts neither add to nor reset this key's streak."""
    key = _tap_key()
    other = _tap(surface="com.other/.Activity", had_effect=True)
    tried = [
        _tap(had_effect=False),
        other,
        _tap(had_effect=False),
        other,
        _tap(had_effect=False),
    ]
    assert consecutive_no_effect_count(tried, key) == 3
    assert detect_repeated_action(tried, _tap(had_effect=False)) is True


def test_consecutive_no_effect_legacy_entries_count_as_no_effect() -> None:
    """Entries recorded before the effect-guard refactor have no had_effect
    field; at read time they count as no-effect (legacy compat)."""
    key = _tap_key()
    tried = [_tap(), _tap()]  # no had_effect field
    assert consecutive_no_effect_count(tried, key) == 2
    assert detect_repeated_action(tried, _tap()) is True


def test_consecutive_no_effect_none_key_returns_zero() -> None:
    assert consecutive_no_effect_count([_tap(had_effect=False)], None) == 0


def test_detect_repeated_action_different_surface_is_progress() -> None:
    tried = [_tap(had_effect=False, surface=_SURFACE)]
    outcome = _tap(had_effect=False, surface="com.other/.Activity")
    assert detect_repeated_action(tried, outcome) is False


# ----------------------------------------------------------------------
# execute-node gate: slider shape never blocked, true loop still caught
# ----------------------------------------------------------------------


def _tap_state(base_state: dict) -> dict:
    state = dict(base_state)
    state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 500]}
    state["grounding_observation"] = {"center": [500, 500]}
    state["observation"] = {"snapshot": {"foreground_activity": _SURFACE}}
    return state


def _config(fake_device, provider=None) -> dict:
    config = {"configurable": {"device_factory": fake_device, "verbose": False}}
    if provider is not None:
        config["configurable"]["locate_provider"] = provider
    return config


def _locate_config(fake_device, provider) -> dict:
    return {
        "configurable": {
            "device_factory": fake_device,
            "verbose": False,
            "locate_provider": provider,
        }
    }


def test_execute_slider_shape_five_effective_swipes_never_blocked(
    base_state, fake_device
) -> None:
    """The real-flight shape: the same swipe key (start/end grid) five times,
    every drag visibly changing the panel (had_effect=True) → never rejected."""
    surface = _SURFACE
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Swipe",
        "start": [500, 900],
        "end": [500, 300],
    }
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Swipe",
            "start": [500.0, 900.0],
            "end": [500.0, 300.0],
            "surface": surface,
            "had_effect": True,
        }
        for _ in range(5)
    ]

    result = execute_node(base_state, _config(fake_device))

    assert result["action_result"]["success"] is True
    assert result.get("repeat_rejected") is not True
    assert any(call[0] == "swipe" for call in fake_device.calls)


def test_execute_true_loop_third_no_effect_attempt_rejected(
    base_state, fake_device
) -> None:
    """Same target, screen never changes (had_effect=False twice) → the 3rd
    attempt is rejected; the trace carries the consecutive_no_effect field."""
    surface = _SURFACE
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        _tap(had_effect=False),
        _tap(had_effect=False),
    ]
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 500]}

    result = execute_node(base_state, _config(fake_device))

    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "repeated_action"
    assert result["repeat_rejected"] is True
    assert result["action_receipt"]["side_effect_receipt"] == {
        "reason_code": "repeated_target_loop",
        "repeat_count": 3,
    }
    assert fake_device.calls == []
    # The rejection itself is written back with had_effect=False so the streak
    # keeps rising on the next identical proposal.
    latest = result["gui_memory"]["tried_actions"][-1]
    assert latest["had_effect"] is False


def test_execute_product_hit_resets_streak_then_allows(
    base_state, fake_device
) -> None:
    """K failed, K productive, K again → not a loop (streak reset)."""
    surface = _SURFACE
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        _tap(had_effect=False),
        _tap(had_effect=True),
        _tap(had_effect=False),
    ]

    result = execute_node(base_state, _config(fake_device))

    assert result["action_result"]["success"] is True
    assert result.get("repeat_rejected") is not True
    assert any(call[0] == "tap" for call in fake_device.calls)


def test_execute_rejection_writes_had_effect_false_and_escalates(
    base_state, fake_device
) -> None:
    """Two legacy (no-field) entries then a rejection: the rejection appends
    had_effect=False, and the next same-key proposal is rejected with an
    escalated repeat_count and the consecutive_no_effect trace field."""
    surface = _SURFACE
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        _tap(),
        _tap(),
    ]

    first = execute_node(base_state, _config(fake_device))
    assert first["repeat_rejected"] is True
    assert first["gui_memory"]["tried_actions"][-1]["had_effect"] is False

    second_state = {**base_state, "gui_memory": first["gui_memory"]}
    second = execute_node(second_state, _config(fake_device))

    assert second["repeat_rejected"] is True
    assert second["action_receipt"]["side_effect_receipt"]["repeat_count"] == 4


# ----------------------------------------------------------------------
# update_gui_memory had_effect wiring
# ----------------------------------------------------------------------


def test_update_gui_memory_records_had_effect_from_verdict() -> None:
    """Reflect-style state: a succeeded verdict writes had_effect=True."""
    memory = update_gui_memory(
        {
            "step_count": 2,
            "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
            "observation": {"snapshot": {"foreground_activity": _SURFACE}},
            "action_result": {"success": True},
            "reflection_verdict": "succeeded",
        },
        current_app="FakeApp",
        screen_id="screen-2",
    )
    assert memory["tried_actions"][-1]["had_effect"] is True


def test_update_gui_memory_failure_derives_had_effect_false() -> None:
    memory = update_gui_memory(
        {
            "step_count": 2,
            "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
            "observation": {"snapshot": {"foreground_activity": _SURFACE}},
            "action_result": {"success": False},
            "failure_cause": "wrong_page",
        },
        current_app="FakeApp",
        screen_id="screen-2",
    )
    assert memory["tried_actions"][-1]["had_effect"] is False


def test_update_gui_memory_explicit_had_effect_overrides_derivation() -> None:
    """A successful locate passes had_effect=True explicitly; a rejection
    passes False — the explicit signal wins over state derivation."""
    state = {
        "step_count": 2,
        "action_parsed": {"action": "Locate", "target_text_hint": "10月1日"},
        "observation": {"snapshot": {"foreground_activity": _SURFACE}},
        "action_result": {"success": True},
    }
    memory = update_gui_memory(
        state, current_app="FakeApp", screen_id="screen-2", had_effect=True
    )
    assert memory["tried_actions"][-1]["had_effect"] is True
    assert "10月1日" not in str(memory["tried_actions"][-1])


# ----------------------------------------------------------------------
# locate: successful locate is progress; fuse 20; failures feed the guard
# ----------------------------------------------------------------------


def test_locate_policy_fuse_is_twenty() -> None:
    assert LOCATE_MAX_PER_RUN == 20


def _locate_state(base_state: dict, **overrides) -> dict:
    state = dict(base_state)
    state["action_parsed"] = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月1日",
        "scope_mark_id": "scope_full",
    }
    state["observation"] = {"snapshot": {"foreground_activity": _SURFACE}}
    state["mark_registry"] = _registry().to_dict()
    state["locate_count"] = 0
    state.update(overrides)
    return state


def test_execute_locate_success_does_not_feed_repeat_guard(
    base_state, fake_device
) -> None:
    """Two successful same-query locates carry had_effect=True; the third
    identical query is still allowed (streak 0) — success is progress."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _locate_state(base_state)

    def run(st: dict) -> dict:
        return execute_node(st, _locate_config(fake_device, provider))

    first = run(state)
    assert first["action_result"]["success"] is True
    state2 = {
        **state,
        "gui_memory": first["gui_memory"],
        "locate_count": first["locate_count"],
        "mark_registry": first["mark_registry"],
    }
    second = run(state2)
    state3 = {
        **state2,
        "gui_memory": second["gui_memory"],
        "locate_count": second["locate_count"],
        "mark_registry": second["mark_registry"],
    }
    third = run(state3)

    assert third["action_result"]["success"] is True
    assert third.get("repeat_rejected") is not True
    tried = third["gui_memory"]["tried_actions"]
    assert all(item["had_effect"] is True for item in tried)
    assert len(provider.requests) == 3


def test_execute_locate_failure_loop_blocked_by_no_effect_guard(
    base_state, fake_device
) -> None:
    """Two failed same-query locates (had_effect=False) → the third is blocked
    by the consecutive-no-effect guard before any provider call."""
    provider = FakeGroundingProvider(failure_code="grounding_no_candidate")
    state = _locate_state(base_state)

    first = execute_node(state, _locate_config(fake_device, provider))
    assert first["action_result"]["success"] is False
    assert first["gui_memory"]["tried_actions"][-1]["had_effect"] is False
    state2 = {
        **state,
        "gui_memory": first["gui_memory"],
        "locate_count": first["locate_count"],
    }
    second = execute_node(state2, _locate_config(fake_device, provider))
    state3 = {
        **state2,
        "gui_memory": second["gui_memory"],
        "locate_count": second["locate_count"],
    }
    third = execute_node(state3, _locate_config(fake_device, provider))

    assert third["action_result"]["success"] is False
    assert third["failure_cause"] == "repeated_action"
    assert third["repeat_rejected"] is True
    assert len(provider.requests) == 2


# ----------------------------------------------------------------------
# reflect tail: the authoritative had_effect write
# ----------------------------------------------------------------------


def test_reflect_tail_writes_had_effect_true_on_productive_step(
    base_state, fake_device
) -> None:
    """A verified step (verdict succeeded) is written with had_effect=True:
    the tried_actions entry carries the per-step effect signal."""
    from tests.graph.test_p5_reflect_skip import (
        _launch_state,
        _programmatic_contract,
        _reflect_config,
        CountingModelClient,
    )

    state = _launch_state(base_state, _programmatic_contract())
    result = reflect_node(
        state, _reflect_config(CountingModelClient(), fake_device)
    )

    assert result["reflection_verdict"] == "succeeded"
    latest = result["gui_memory"]["tried_actions"][-1]
    assert latest["action"] == "Launch"
    assert latest["had_effect"] is True


def test_reflect_tail_writes_had_effect_true_on_screen_change(
    base_state, fake_device
) -> None:
    """Screen change signal: same partial verdict reports had_effect=False
    when the before/after hashes match (dead loop) and had_effect=True when
    the screen actually moved."""
    from tests.graph.test_p5_reflect_skip import (
        _launch_state,
        _programmatic_contract,
        _reflect_config,
        CountingModelClient,
    )

    # First run (observation=None) captures the deterministic after-hash.
    state = _launch_state(base_state, _programmatic_contract())
    first = reflect_node(state, _reflect_config(CountingModelClient(), fake_device))
    after_hash = first["screen_hash"]
    assert after_hash is not None

    # Dead-loop shaped second run: before hash equals after hash, model
    # verdict partial → no screen change, no observation, no succeeded →
    # had_effect=False (the streak keeps rising). Real state shape: the
    # before-frame hash lives at the state top level (plan writes
    # state["screen_hash"]); Observation.to_dict() has no top-level
    # screen_hash — putting one under "observation" would be a shape that
    # never exists in real flights.
    state2 = {
        **state,
        "screen_hash": after_hash,
        "observation": {"snapshot": {}},
        "gui_memory": first["gui_memory"],
    }
    second = reflect_node(
        state2,
        _reflect_config(
            CountingModelClient(),
            fake_device,
            skip_reflect_on_high_confidence=False,
        ),
    )
    assert second["reflection_verdict"] == "partial"
    assert second["gui_memory"]["tried_actions"][-1]["had_effect"] is False

    # Before hash differs from after hash (a genuinely moved screen): the same
    # partial verdict now reports had_effect=True.
    state3 = {
        **state2,
        "screen_hash": "other-hash",
        "gui_memory": second["gui_memory"],
    }
    third = reflect_node(
        state3,
        _reflect_config(
            CountingModelClient(),
            fake_device,
            skip_reflect_on_high_confidence=False,
        ),
    )
    assert third["reflection_verdict"] == "partial"
    assert third["gui_memory"]["tried_actions"][-1]["had_effect"] is True


# ----------------------------------------------------------------------
# Launch repeat identity (F4): digested app term on the same surface
# ----------------------------------------------------------------------


def test_launch_repeat_key_digests_app_term() -> None:
    """Launch keys on (action, sanitized app digest, surface): same app +
    same surface → same key; a different app → a different key; the raw app
    term never appears in the key. The digest is produced at write time (both
    execute's candidate_repeat and update_gui_memory digest the app term)."""
    key = repeated_action_key(
        {"action": "Launch", "app": locate_hint_digest("未知应用"), "surface": _SURFACE}
    )
    assert key == ("Launch", locate_hint_digest("未知应用"), _SURFACE)
    other = repeated_action_key(
        {"action": "Launch", "app": locate_hint_digest("另一个应用"), "surface": _SURFACE}
    )
    assert other != key
    same_surface_other_app = repeated_action_key(
        {
            "action": "Launch",
            "app": locate_hint_digest("未知应用"),
            "surface": "com.other/.Activity",
        }
    )
    assert same_surface_other_app != key
    assert "未知应用" not in str(key)
    # no digest (missing/empty app) -> no repeat key, guard stays blind-safe
    assert repeated_action_key({"action": "Launch", "surface": _SURFACE}) is None


def test_execute_launch_loop_blocked_by_no_effect_guard(
    base_state, fake_device
) -> None:
    """Two no-effect launches of the same unknown app on the same surface →
    the third is rejected by the consecutive-no-effect guard (before F4 the
    Launch key was always None, so the guard never counted it). tried_actions
    carries the digest (P0 #10), matching what update_gui_memory writes."""
    surface = _SURFACE
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Launch",
            "app": locate_hint_digest("未知应用"),
            "surface": surface,
            "had_effect": False,
        },
        {
            "action": "Launch",
            "app": locate_hint_digest("未知应用"),
            "surface": surface,
            "had_effect": False,
        },
    ]
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Launch",
        "app": "未知应用",
        "package_candidates": ["com.example.unknown.app"],
    }

    result = execute_node(base_state, _config(fake_device))

    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "repeated_action"
    assert result["repeat_rejected"] is True
    assert fake_device.calls == []


def test_execute_launch_different_app_not_blocked(base_state, fake_device) -> None:
    """Launching a different app on the same surface is progress — the repeat
    guard never counts cross-app attempts against each other."""
    surface = _SURFACE
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Launch",
            "app": locate_hint_digest("未知应用"),
            "surface": surface,
            "had_effect": False,
        },
        {
            "action": "Launch",
            "app": locate_hint_digest("未知应用"),
            "surface": surface,
            "had_effect": False,
        },
    ]
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Launch",
        "app": "另一个应用",
        "package_candidates": ["com.example.unknown.app"],
    }

    result = execute_node(base_state, _config(fake_device))

    assert result["action_result"]["success"] is True
    assert result.get("repeat_rejected") is not True
    assert any(call[0] == "launch_app" for call in fake_device.calls)


# ----------------------------------------------------------------------
# context block: locate countdown gone from the plan block
# ----------------------------------------------------------------------


def test_plan_context_block_has_no_locate_countdown(base_state) -> None:
    state = {
        **base_state,
        "step_count": 3,
        "max_steps": 20,
        "locate_count": 2,
    }
    block, _metrics = build_plan_context_block(state)
    assert "locate 剩余" not in block
    assert "locate " not in block
    assert "/20 left" not in block
