"""Conditional edges for the Plan-Execute-Reflect graph."""

from typing import Literal

from phone_agent.graph.state import AgentState


def should_continue(state: AgentState) -> Literal["end", "replan"]:
    """
    Decide whether to continue looping or end after reflect node.

    Routes:
    - "end" if finished, error, or max_steps reached
    - "replan" otherwise (go back to plan node)
    """
    if state.get("finished"):
        return "end"
    if state.get("error"):
        return "end"
    if state["step_count"] >= state["max_steps"]:
        return "end"
    return "replan"


def after_execute(state: AgentState) -> Literal["reflect", "replan", "confirm", "takeover", "end"]:
    """
    Decide the route after execute node.

    Routes:
    - "end" if action is finish or execution resulted in finish
    - "confirm" if action is Tap with message (sensitive operation)
    - "takeover" if action is Take_over
    - "replan" if action is a skip type (Wait, Note, Call_API, Interact)
    - "reflect" otherwise
    """
    # Check pending interrupt first (for resume path)
    pending = state.get("pending_interrupt")
    if pending == "confirmation":
        return "confirm"
    if pending == "takeover":
        return "takeover"

    action = state.get("action_parsed")
    if not action:
        return "end"
    if action.get("_metadata") == "finish":
        return "end"

    # CRITICAL-1: action_confirmed check BEFORE HITL routing
    # If the action was already confirmed (pending_execute branch executed it),
    # skip HITL and go straight to reflect
    if state.get("action_confirmed"):
        return "reflect"

    # Human-in-the-Loop routing
    if action.get("action") == "Take_over":
        return "takeover"
    if action.get("action") == "Tap" and "message" in action:
        return "confirm"

    # Skip reflect for these action types
    skip_actions = {"Wait", "Note", "Call_API", "Interact"}
    if action.get("action") in skip_actions:
        return "replan"

    return "reflect"


def after_interrupt(state: AgentState) -> Literal["reflect", "execute", "end"]:
    """
    Decide the route after confirm/takeover interrupt node.

    Routes:
    - "end" if user cancelled or task finished
    - "execute" if confirm accepted and pending action needs dispatch
    - "reflect" otherwise (continue to reflect node)
    """
    if state.get("finished"):
        return "end"

    # BUG 2 fix: if confirm accepted and there's a pending action, route to execute
    if state.get("pending_execute") and state.get("interrupt_result") is True:
        return "execute"

    return "reflect"
