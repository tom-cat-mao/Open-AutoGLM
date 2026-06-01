from phone_agent.graph.edges import after_execute, after_interrupt, should_continue


def test_after_execute_routes_pending_interrupts_first(base_state) -> None:
    base_state["pending_interrupt"] = "confirmation"
    assert after_execute(base_state) == "confirm"

    base_state["pending_interrupt"] = "takeover"
    assert after_execute(base_state) == "takeover"


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


def test_after_execute_routes_skip_actions_to_replan(base_state) -> None:
    for action in ("Wait", "Note", "Call_API", "Interact"):
        base_state["action_parsed"] = {"_metadata": "do", "action": action}
        base_state["action_confirmed"] = False
        assert after_execute(base_state) == "replan"


def test_after_interrupt_confirm_accept_routes_pending_execute(base_state) -> None:
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True

    assert after_interrupt(base_state) == "execute"


def test_after_interrupt_cancel_or_normal_routes_by_finished(base_state) -> None:
    assert after_interrupt(base_state) == "reflect"
    base_state["finished"] = True
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
