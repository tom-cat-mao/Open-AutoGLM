"""Goal node: compile a declarative GoalContract once at task start.

Inserted between START and plan in the graph topology.  On subsequent
replan loops, it is a no-op (returns ``{}``) when the contract is already
compiled and no recompilation is requested.
"""

from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from phone_agent.graph.goal_compiler import compile_goal_contract
from phone_agent.graph.trace import emit_trace

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def goal_node(state: "AgentState", config: RunnableConfig) -> dict:
    """Compile or reuse the task goal contract.

    Routing:
    - Already compiled and not needs_recompile → no-op (returns ``{}``).
    - needs_recompile or status pending → run compilation chain.
    - Optional ``require_goal_approval`` → interrupt for HITL review.
    """
    configurable = config.get("configurable", {}) if config else {}
    current_status = state.get("goal_contract_status")
    needs_recompile = bool(state.get("needs_recompile"))

    if current_status in {"compiled", "user_override"} and not needs_recompile:
        # Already have a usable contract — no-op
        return {}

    task = str(state.get("task") or "")
    step_count = int(state.get("step_count") or 0)

    emit_trace(
        config,
        state,
        "goal",
        "goal_compile_start",
        {
            "task_hash_length": len(task),
            "step_count": step_count,
            "previous_status": current_status,
            "needs_recompile": needs_recompile,
        },
    )

    contract = compile_goal_contract(state, config)

    # If LLM failed and we fell back to heuristic, trace it explicitly.
    if contract.compile_source == "heuristic_fallback":
        emit_trace(
            config,
            state,
            "goal",
            "goal_compile_fallback",
            {
                "compile_source": contract.compile_source,
                "compile_status": contract.compile_status,
                "criterion_count": len(contract.success_criteria),
            },
        )

    # Optional HITL goal approval
    if configurable.get("require_goal_approval"):
        result = interrupt(
            {
                "type": "goal_approval",
                "goal_contract": contract.to_trace_payload(),
                "prompt": "Approve the goal contract? (Y/N/Edit): ",
            }
        )
        if isinstance(result, str) and result.upper() in {"N", "NO", "EDIT"}:
            # User rejected — fall back to heuristic weak contract
            from phone_agent.graph.goal_compiler import HeuristicGoalCompiler
            from dataclasses import replace

            contract = replace(
                HeuristicGoalCompiler().compile(task=task),
                compile_source="heuristic_user_rejected",
            )
        emit_trace(
            config,
            state,
            "goal",
            "goal_approval_result",
            {"approved": not (isinstance(result, str) and result.upper() in {"N", "NO", "EDIT"})},
        )

    contract_dict = contract.to_dict()

    emit_trace(
        config,
        state,
        "goal",
        "goal_compile_result",
        {
            "goal_contract": contract.to_trace_payload(),
            "compile_source": contract.compile_source,
            "compile_status": contract.compile_status,
            "compile_attempts": contract.compile_attempts,
            "criterion_count": len(contract.success_criteria),
            "verification_strategy": contract.verification_strategy,
        },
    )

    return {
        "goal_contract": contract_dict,
        "goal_contract_status": contract.compile_status,
        "goal_compile_source": contract.compile_source,
        "goal_compile_attempts": contract.compile_attempts,
        "needs_recompile": False,
    }
