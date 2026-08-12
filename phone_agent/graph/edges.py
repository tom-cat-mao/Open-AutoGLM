"""Conditional edges for the Plan-Execute-Reflect graph."""

from typing import Literal

from phone_agent.actions.capability import get_tool_capability
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY
from phone_agent.graph.state import AgentState

OBSERVATION_RETRY_LIMIT = int(
    DEFAULT_VERIFICATION_POLICY.value("observation_retry_limit")
)
def after_goal(state: AgentState) -> Literal["plan", "end", "takeover"]:
    """Fail closed before Plan when requirement/contract validation failed."""

    if state.get("finished") or state.get("error"):
        return "end"
    if state.get("goal_contract_status") == "failed":
        if state.get("retry_policy") == "takeover":
            return "takeover"
        return "end"
    return "plan"


def after_plan(state: AgentState) -> Literal["execute", "replan"]:
    """Route validation/adapter guidance replans without executing a null action."""

    failure = state.get("parse_failure")
    if (
        not state.get("finished")
        and not state.get("error")
        and not state.get("action_parsed")
        and isinstance(failure, dict)
        and failure.get("layer") in {"adapter", "validation"}
        and int(state.get("validation_replan_count") or 0) > 0
    ):
        return "replan"
    return "execute"


def should_continue(
    state: AgentState,
) -> Literal["end", "replan", "takeover", "acceptance"]:
    """
    Decide whether to continue looping or end after reflect node.

    Routes:
    - "end" if finished or errored — always wins (P0 #5)
    - "acceptance" once when the step budget is exhausted and the goal
      contract was compiled but never validated (budget-forced acceptance,
      model-delegation refactor 2.1). The acceptance node itself sets
      ``budget_acceptance_done`` so this fires at most once per run; if the
      forced claim is rejected, ``after_acceptance`` still routes to "end"
      at max_steps, so this never loops.
    - "takeover" if reflect requested a takeover interrupt, or observation
      infrastructure failures exceeded the retry limit
    - "replan" otherwise (route to goal → plan; goal_node no-ops when the
      contract is already compiled and needs_recompile is False, otherwise
      it re-runs the compilation chain)
    """
    if state.get("finished"):
        return "end"
    if state.get("error"):
        return "end"
    if state["step_count"] >= state["max_steps"]:
        if (
            state.get("goal_contract_status") == "compiled"
            and not state.get("budget_acceptance_done")
        ):
            return "acceptance"
        return "end"
    if state.get("pending_interrupt") == "takeover":
        return "takeover"
    if int(state.get("observation_retry_count") or 0) >= OBSERVATION_RETRY_LIMIT:
        return "takeover"
    return "replan"


def after_execute(
    state: AgentState,
) -> Literal["reflect", "acceptance", "replan", "confirm", "takeover", "end"]:
    """
    Decide the route after execute node.

    Routes:
    - "end" if the state is terminal (finished or errored) — always wins
    - "replan" if the repeat guard rejected the action (system decision: no
      reflect, no failure_memory, straight back to planning)
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

    # A repeat-guard rejection is a system decision, not an action failure:
    # route straight back to planning without reflect (no verdict, no
    # failure_memory write) so the system's own decision is not recorded as
    # the action failing. The rejected action was already counted in
    # gui_memory.tried_actions by execute_node, and the rejection reason +
    # count reach the next plan prompt through avoid_repeating /
    # last_action_outcome. plan_node clears the flag on its next run.
    if state.get("repeat_rejected"):
        return "replan"

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
    if state["step_count"] >= state["max_steps"]:
        return "end"
    if state.get("pending_interrupt") == "takeover":
        return "takeover"
    if int(state.get("observation_retry_count") or 0) >= OBSERVATION_RETRY_LIMIT:
        return "takeover"
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
