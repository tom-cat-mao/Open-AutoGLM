from phone_agent.graph.edges import (
    after_execute,
    after_goal,
    after_interrupt,
    should_continue,
)


def test_after_execute_routes_pending_interrupts_first(base_state) -> None:
    base_state["pending_interrupt"] = "confirmation"
    assert after_execute(base_state) == "confirm"

    base_state["pending_interrupt"] = "takeover"
    assert after_execute(base_state) == "takeover"


def test_after_execute_terminal_state_wins_over_stale_pending_interrupt(
    base_state,
) -> None:
    """Terminal guard must beat pending_interrupt routing.

    If a previous step set pending_interrupt="confirmation" (e.g. HITL
    confirm path) and the current execute_node returns finished=True /
    error without clearing it, after_execute must route to "end" — not
    "confirm". Mirrors the contract of should_continue() and
    after_interrupt(), which both treat finished/error as terminal.
    """
    base_state["pending_interrupt"] = "confirmation"
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [1, 2],
        "message": "pay",
    }

    base_state["finished"] = True
    assert after_execute(base_state) == "end"

    base_state["finished"] = False
    base_state["error"] = "Action rejected by safety gate"
    assert after_execute(base_state) == "end"

    base_state["pending_interrupt"] = "takeover"
    base_state["error"] = None
    base_state["finished"] = True
    assert after_execute(base_state) == "end"


def test_after_execute_confirmed_sensitive_tap_goes_to_reflect(base_state) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [1, 2],
        "message": "pay",
    }
    base_state["action_confirmed"] = True

    assert after_execute(base_state) == "reflect"


def test_after_execute_routes_sensitive_tap_to_confirm(base_state) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [1, 2],
        "message": "pay",
    }

    assert after_execute(base_state) == "confirm"


def test_after_execute_routes_ui_external_and_wait_actions_to_reflect(
    base_state,
) -> None:
    for action in (
        "Tap",
        "Type",
        "Swipe",
        "Back",
        "Home",
        "Launch",
        "Wait",
        "Call_API",
        "Interact",
    ):
        base_state["action_parsed"] = {"_metadata": "do", "action": action}
        base_state["action_confirmed"] = False
        assert after_execute(base_state) == "reflect"


def test_after_execute_only_skips_internal_non_progress_capability(
    base_state, monkeypatch
) -> None:
    from phone_agent.actions.capability import ToolCapability
    import phone_agent.graph.edges as edges_module

    capability = ToolCapability(
        action_name="Internal",
        implementation_status="implemented",
        side_effect_kind="none",
        observation_effect="none",
        required_postconditions=(),
        retry_safety="safe",
        can_advance_goal=False,
    )
    monkeypatch.setattr(edges_module, "get_tool_capability", lambda _: capability)
    base_state["action_parsed"] = {"_metadata": "do", "action": "Internal"}

    assert after_execute(base_state) == "replan"


def test_after_execute_routes_finish_claim_to_reflect(base_state) -> None:
    base_state["action_parsed"] = {"_metadata": "finish", "message": "done"}
    base_state["pending_finish"] = True

    assert after_execute(base_state) == "reflect"


def test_after_execute_stale_finish_without_pending_finish_ends(base_state) -> None:
    base_state["action_parsed"] = {"_metadata": "finish", "message": "old"}
    base_state["pending_finish"] = False

    assert after_execute(base_state) == "end"


def test_after_interrupt_confirm_accept_routes_pending_execute(base_state) -> None:
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True

    assert after_interrupt(base_state) == "execute"


def test_after_interrupt_cancel_or_normal_routes_by_finished(base_state) -> None:
    assert after_interrupt(base_state) == "reflect"
    base_state["finished"] = True
    assert after_interrupt(base_state) == "end"


def test_after_interrupt_terminal_error_wins_over_pending_execute(base_state) -> None:
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True
    base_state["error"] = "Action rejected by safety gate"

    assert after_interrupt(base_state) == "end"


def test_should_continue_ends_on_error_finished_or_max_steps(base_state) -> None:
    assert should_continue(base_state) == "replan"
    base_state["finished"] = True
    assert should_continue(base_state) == "end"
    base_state["finished"] = False
    base_state["error"] = "boom"
    assert should_continue(base_state) == "end"
    base_state["error"] = None
    base_state["step_count"] = base_state["max_steps"]
    assert should_continue(base_state) == "end"


def test_should_continue_routes_takeover_after_terminal_guard(base_state) -> None:
    base_state["pending_interrupt"] = "takeover"
    assert should_continue(base_state) == "takeover"

    base_state["finished"] = True
    assert should_continue(base_state) == "end"


def test_reflect_conditional_edges_include_takeover_route() -> None:
    from phone_agent.graph.builder import create_agent_graph

    graph = create_agent_graph()
    edges = graph.get_graph().edges
    assert any(edge.source == "reflect" and edge.target == "takeover" for edge in edges)


def test_after_goal_fails_closed_before_plan(base_state) -> None:
    assert after_goal(base_state) == "plan"
    base_state["goal_contract_status"] = "failed"
    assert after_goal(base_state) == "end"
    base_state["goal_contract_status"] = "compiled"
    base_state["error"] = "Goal contract rejected"
    assert after_goal(base_state) == "end"
