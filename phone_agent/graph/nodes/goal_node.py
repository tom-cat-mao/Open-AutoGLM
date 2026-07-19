"""Goal node: compile a declarative GoalContract once at task start.

Inserted between START and plan in the graph topology.  On subsequent
replan loops, it is a no-op (returns ``{}``) when the contract is already
compiled and no recompilation is requested.
"""

from typing import TYPE_CHECKING

from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from phone_agent.graph.goal_compiler import GoalCompilationError, compile_goal_contract
from phone_agent.graph.goal_requirements import (
    ContractAdequacyValidator,
    TaskRequirementExtractor,
)
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
    runtime_goal_context = configurable.get("runtime_goal_context")

    if current_status in {"compiled", "user_override"} and not needs_recompile:
        if runtime_goal_context is not None and _ensure_runtime_goal(state, config):
            return {}
        return _runtime_goal_failure("runtime_goal_binding_unavailable")

    if runtime_goal_context is None:
        return _runtime_goal_failure("runtime_goal_context_missing")

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

    requirements = TaskRequirementExtractor().extract(task)
    try:
        contract = compile_goal_contract(state, config)
    except GoalCompilationError as exc:
        emit_trace(
            config,
            state,
            "goal",
            "goal_compile_failed",
            {
                "failure_cause": exc.code,
                "requirement_set": requirements.safe_projection(),
            },
        )
        return {
            "goal_contract": None,
            "goal_contract_status": "failed",
            "goal_compile_source": "external",
            "goal_compile_attempts": 1,
            "task_requirement_set": requirements.safe_projection(),
            "contract_adequacy_status": "inadequate",
            "contract_adequacy_reasons": [exc.code],
            "failure_cause": "unsupported_semantics",
            "error_layer": "goal",
            "error_code": exc.code,
            "recoverable": True,
            "retry_policy": "takeover",
            "error": f"Goal contract rejected: {exc.code}",
            "finished": True,
            "needs_recompile": False,
        }
    adequacy = ContractAdequacyValidator().validate(requirements, contract)
    if adequacy.status != "adequate":
        failure_cause = (
            "needs_goal_clarification"
            if adequacy.status == "needs_clarification"
            else "unsupported_semantics"
        )
        emit_trace(
            config,
            state,
            "goal",
            "goal_contract_rejected",
            {
                "failure_cause": failure_cause,
                "contract_adequacy": {
                    "status": adequacy.status,
                    "reason_codes": list(adequacy.reason_codes),
                },
                "requirement_set": requirements.safe_projection(),
            },
        )
        return {
            "goal_contract": None,
            "goal_contract_status": "failed",
            "goal_compile_source": contract.compile_source,
            "goal_compile_attempts": contract.compile_attempts,
            "task_requirement_set": requirements.safe_projection(),
            "contract_adequacy_status": adequacy.status,
            "contract_adequacy_reasons": list(adequacy.reason_codes),
            "failure_cause": failure_cause,
            "error_layer": "goal",
            "error_code": failure_cause,
            "recoverable": True,
            "retry_policy": "takeover",
            "error": f"Goal contract rejected: {failure_cause}",
            "finished": True,
            "needs_recompile": False,
        }

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
            adequacy = ContractAdequacyValidator().validate(requirements, contract)
            if adequacy.status != "adequate":
                return {
                    "goal_contract": None,
                    "goal_contract_status": "failed",
                    "goal_compile_source": contract.compile_source,
                    "goal_compile_attempts": contract.compile_attempts,
                    "task_requirement_set": requirements.safe_projection(),
                    "contract_adequacy_status": adequacy.status,
                    "contract_adequacy_reasons": list(adequacy.reason_codes),
                    "failure_cause": "unsupported_semantics",
                    "error_layer": "goal",
                    "error_code": "goal_approval_replacement_inadequate",
                    "recoverable": True,
                    "retry_policy": "takeover",
                    "error": "Replacement goal contract is inadequate",
                    "finished": True,
                    "needs_recompile": False,
                }
        emit_trace(
            config,
            state,
            "goal",
            "goal_approval_result",
            {
                "approved": not (
                    isinstance(result, str) and result.upper() in {"N", "NO", "EDIT"}
                )
            },
        )

    runtime_reference = None
    try:
        runtime_reference = runtime_goal_context.register(
            task=task, contract=contract, requirements=requirements
        )
    except (AttributeError, ValueError):
        emit_trace(
            config,
            state,
            "goal",
            "goal_runtime_binding_failed",
            {"failure_cause": "runtime_goal_binding_invalid"},
        )
        return _runtime_goal_failure("runtime_goal_binding_invalid")
    contract_dict = contract.to_state_payload(runtime_reference=runtime_reference)

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
            "requirement_set": requirements.safe_projection(),
            "contract_adequacy": {
                "status": adequacy.status,
                "reason_codes": list(adequacy.reason_codes),
            },
        },
    )

    return {
        "goal_contract": contract_dict,
        "goal_contract_status": contract.compile_status,
        "goal_compile_source": contract.compile_source,
        "goal_compile_attempts": contract.compile_attempts,
        "task_requirement_set": requirements.safe_projection(),
        "contract_adequacy_status": adequacy.status,
        "contract_adequacy_reasons": list(adequacy.reason_codes),
        "needs_recompile": False,
    }


def _ensure_runtime_goal(state: "AgentState", config: RunnableConfig) -> bool:
    from phone_agent.graph.goal import ensure_goal_contract

    return ensure_goal_contract(state, config) is not None


def _runtime_goal_failure(code: str) -> dict:
    return {
        "goal_contract": None,
        "goal_contract_status": "failed",
        "contract_adequacy_status": "inadequate",
        "contract_adequacy_reasons": [code],
        "failure_cause": "unsupported_semantics",
        "error_layer": "goal",
        "error_code": "goal_contract_invalid",
        "recoverable": True,
        "retry_policy": "takeover",
        "error": f"Goal contract invalid: {code}",
        "finished": True,
        "needs_recompile": False,
    }
