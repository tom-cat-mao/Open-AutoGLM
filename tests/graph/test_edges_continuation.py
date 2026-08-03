"""F2 window-budget edge routing (edges stay pure; reads only)."""

from phone_agent.graph.edges import after_acceptance, should_continue


def _window_state(**overrides) -> dict:
    state = {
        "finished": False,
        "error": None,
        "step_count": 20,
        "max_steps": 20,
        "goal_contract_status": "compiled",
        "budget_acceptance_done": False,
        "absolute_max_steps": 60,
        "continuation_count": 0,
        "pending_interrupt": None,
        "observation_retry_count": 0,
    }
    state.update(overrides)
    return state


def test_after_acceptance_replans_when_grant_grew_the_window() -> None:
    """After a grant the node wrote max_steps=30 and reset the done flag; the
    pure edge must route back to planning, not end."""
    merged = _window_state(max_steps=30, budget_acceptance_done=False)

    assert after_acceptance(merged) == "replan"


def test_after_acceptance_ends_without_grant_at_window_end() -> None:
    merged = _window_state(step_count=20, max_steps=20)

    assert after_acceptance(merged) == "end"


def test_after_acceptance_ends_at_absolute_ceiling() -> None:
    merged = _window_state(
        step_count=60, max_steps=60, absolute_max_steps=60
    )

    assert after_acceptance(merged) == "end"


def test_after_acceptance_terminal_guard_wins_first() -> None:
    """P0 #5: finished/error beats window routing even after a grant."""
    merged = _window_state(max_steps=30, finished=True)
    assert after_acceptance(merged) == "end"

    merged = _window_state(max_steps=30, error="boom")
    assert after_acceptance(merged) == "end"


def test_should_continue_routes_new_window_to_acceptance_again() -> None:
    """A granted window resets budget_acceptance_done: at the new window end the
    run gets a second forced acceptance instead of ending."""
    state = _window_state(
        step_count=30,
        max_steps=30,
        budget_acceptance_done=False,
    )

    assert should_continue(state) == "acceptance"


def test_should_continue_ends_when_grant_flag_consumed() -> None:
    state = _window_state(
        step_count=30,
        max_steps=30,
        budget_acceptance_done=True,
    )

    assert should_continue(state) == "end"


def test_should_continue_absolute_ceiling_still_routes_acceptance() -> None:
    """At the absolute ceiling (no grant possible) the forced acceptance still
    fires once; the acceptance node attributes absolute_budget_exhausted."""
    state = _window_state(
        step_count=60,
        max_steps=60,
        absolute_max_steps=60,
        budget_acceptance_done=False,
    )

    assert should_continue(state) == "acceptance"


def test_should_continue_p0_guard_wins_over_budget_routes() -> None:
    """P0 #5: terminal states never route to acceptance, even at the ceiling."""
    state = _window_state(step_count=60, max_steps=60, finished=True)
    assert should_continue(state) == "end"

    state = _window_state(step_count=60, max_steps=60, error="boom")
    assert should_continue(state) == "end"


def test_should_continue_takeover_survives_budget_routes() -> None:
    state = _window_state(
        step_count=10, max_steps=30, pending_interrupt="takeover"
    )
    assert should_continue(state) == "takeover"


def test_should_continue_budget_wins_over_stale_takeover() -> None:
    """At the window boundary the budget route wins over a stale interrupt;
    P0 #5 still wins over everything."""
    state = _window_state(step_count=30, max_steps=30, pending_interrupt="takeover")
    assert should_continue(state) == "acceptance"
