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


def after_execute(
    state: AgentState,
) -> Literal["reflect", "replan", "confirm", "takeover", "end"]:
    """
    Decide the route after execute node.

    Routes:
    - "end" if the state is terminal (finished or errored) — always wins
    - "confirm" / "takeover" if a pending HITL interrupt is waiting to be
      dispatched (resume path only — terminal guard above prevents this
      from firing when the run is already done)
    - "end" if action is finish or missing
    - "reflect" if action_confirmed (already dispatched on resume)
    - "takeover" if action is Take_over
    - "confirm" if action is Tap with message (sensitive operation)
    - "replan" if action is a skip type (Wait, Note, Call_API, Interact)
    - "reflect" otherwise
    """
    # Terminal guard: finished/error always routes to "end".
    # Must come BEFORE the pending_interrupt check so that a stale
    # pending_interrupt left over from a previous step cannot misroute
    # a terminal state into confirm/takeover. Mirrors the contract of
    # should_continue() and after_interrupt().
    if state.get("finished") or state.get("error"):
        return "end"

    # Check pending interrupt (resume path — safe now that terminal states
    # have been filtered out above).
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
    - "end" if user cancelled or task finished/errored
    - "execute" if confirm accepted and pending action needs dispatch
    - "reflect" otherwise (continue to reflect node)
    """
    if state.get("finished") or state.get("error"):
        return "end"

    # BUG 2 fix: if confirm accepted and there's a pending action, route to execute
    if state.get("pending_execute") and state.get("interrupt_result") is True:
        return "execute"

    return "reflect"
