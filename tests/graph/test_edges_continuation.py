"""Resource-fuse edge routing (edges stay pure; reads only)."""

from phone_agent.graph.edges import after_acceptance, should_continue


def _window_state(**overrides) -> dict:
    state = {
        "finished": False,
        "error": None,
        "step_count": 20,
        "max_steps": 20,
        "step_cap": 20,
        "goal_contract_status": "compiled",
        "continuation_count": 0,
        "pending_interrupt": None,
        "observation_retry_count": 0,
    }
    state.update(overrides)
    return state


def test_after_acceptance_replans_when_fuse_not_hit() -> None:
    merged = _window_state(step_count=20, max_steps=30, step_cap=30)

    assert after_acceptance(merged) == "replan"


def test_after_acceptance_ends_at_step_cap() -> None:
    merged = _window_state(step_count=20, max_steps=20)

    assert after_acceptance(merged) == "end"


def test_after_acceptance_ends_at_wall_clock_cap() -> None:
    merged = _window_state(
        step_count=1,
        max_steps=20,
        step_cap=20,
        wall_clock_cap_started_at=1,
        wall_clock_cap_seconds=1,
    )

    assert after_acceptance(merged) == "end"


def test_after_acceptance_terminal_guard_wins_first() -> None:
    """P0 #5: finished/error beats window routing even after a grant."""
    merged = _window_state(max_steps=30, finished=True)
    assert after_acceptance(merged) == "end"

    merged = _window_state(max_steps=30, error="boom")
    assert after_acceptance(merged) == "end"


def test_should_continue_routes_before_fuse_to_replan() -> None:
    state = _window_state(step_count=29, max_steps=30, step_cap=30)

    assert should_continue(state) == "replan"


def test_should_continue_ends_at_step_cap() -> None:
    state = _window_state(step_count=30, max_steps=30, step_cap=30)

    assert should_continue(state) == "end"


def test_should_continue_ends_at_wall_clock_cap() -> None:
    state = _window_state(
        step_count=3,
        max_steps=60,
        step_cap=60,
        wall_clock_cap_started_at=1,
        wall_clock_cap_seconds=1,
    )

    assert should_continue(state) == "end"


def test_should_continue_p0_guard_wins_over_fuse_routes() -> None:
    state = _window_state(step_count=60, max_steps=60, finished=True)
    assert should_continue(state) == "end"

    state = _window_state(step_count=60, max_steps=60, error="boom")
    assert should_continue(state) == "end"


def test_should_continue_takeover_survives_before_fuse() -> None:
    state = _window_state(
        step_count=10, max_steps=30, pending_interrupt="takeover"
    )
    assert should_continue(state) == "takeover"


def test_should_continue_fuse_wins_over_stale_takeover() -> None:
    state = _window_state(step_count=30, max_steps=30, pending_interrupt="takeover")
    assert should_continue(state) == "end"
