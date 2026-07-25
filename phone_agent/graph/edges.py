"""Conditional edges for the Plan-Execute-Reflect graph."""

from typing import Literal

from phone_agent.actions.capability import get_tool_capability
from phone_agent.graph.state import AgentState


def after_goal(state: AgentState) -> Literal["plan", "end", "takeover"]:
    """Fail closed before Plan when requirement/contract validation failed."""

    if state.get("finished") or state.get("error"):
        return "end"
    if state.get("goal_contract_status") == "failed":
        if state.get("retry_policy") == "takeover":
            return "takeover"
        return "end"
    return "plan"


def should_continue(state: AgentState) -> Literal["end", "replan", "takeover"]:
    """
    Decide whether to continue looping or end after reflect node.

    Routes:
    - "end" if finished, error, or max_steps reached
    - "takeover" if reflect requested a takeover interrupt
    - "replan" otherwise (route to goal → plan; goal_node no-ops when the
      contract is already compiled and needs_recompile is False, otherwise
      it re-runs the compilation chain)
    """
    if state.get("finished"):
        return "end"
    if state.get("error"):
        return "end"
    if state.get("pending_interrupt") == "takeover":
        return "takeover"
    if state["step_count"] >= state["max_steps"]:
        return "end"
    return "replan"


def after_execute(
    state: AgentState,
) -> Literal["reflect", "acceptance", "replan", "confirm", "takeover", "end"]:
    """
    Decide the route after execute node.

    Routes:
    - "end" if the state is terminal (finished or errored) — always wins
    - "confirm" / "takeover" if a pending HITL interrupt is waiting to be
      dispatched (resume path only — terminal guard above prevents this
      from firing when the run is already done)
    - "acceptance" if a finish claim is pending validation
    - "end" if action is missing
    - "reflect" if action_confirmed (already dispatched on resume)
    - "takeover" if action is Take_over
    - "confirm" if action is Tap with message (sensitive operation)
    - "replan" only for an implemented internal capability that cannot affect
      observations or Goal progress
    - "reflect" for every UI/external action and Wait
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
    # A finish claim asks whether the whole task is done, not whether one
    # action worked, so it goes to acceptance rather than action reflection.
    if state.get("pending_finish"):
        return "acceptance"
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

    capability = get_tool_capability(str(action.get("action")))
    if capability is None:
        return "end"
    if not capability.requires_reobservation:
        return "replan"

    return "reflect"


def after_acceptance(state: AgentState) -> Literal["replan", "takeover", "end"]:
    """
    Decide the route after the acceptance node.

    Routes:
    - "end" if the goal was satisfied (or the run errored out)
    - "takeover" if acceptance escalated to a human
    - "replan" otherwise — the claim was rejected, so keep working
    """
    if state.get("finished") or state.get("error"):
        return "end"
    if state.get("pending_interrupt") == "takeover":
        return "takeover"
    if state["step_count"] >= state["max_steps"]:
        return "end"
    return "replan"


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
