"""Human-in-the-Loop confirmation node using LangGraph interrupt().

Replaces ActionHandler.confirmation_callback with a resumable interrupt.
"""

from typing import TYPE_CHECKING
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from phone_agent.graph.trace import emit_trace

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def confirm_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Confirmation node: pause graph execution and ask for user confirmation.

    Uses LangGraph interrupt() to pause the graph. On resume, the user's
    response (True/False) is stored in state, and execution continues.

    If user rejects confirmation, set finished=True to end the task.
    """
    message = state.get("interrupt_message") or "Sensitive operation"
    emit_trace(config, state, "confirm", "confirm_interrupt", {"message": message})
    # interrupt() raises GraphInterrupt on first call, returns resume value on second
    result = interrupt(
        {
            "type": "confirmation",
            "message": message,
            "prompt": f"Sensitive operation: {message}\nConfirm? (Y/N): ",
        }
    )

    # Parse result
    if isinstance(result, bool):
        confirmed = result
    elif isinstance(result, str):
        confirmed = result.upper() in ("Y", "YES", "TRUE", "1")
    else:
        confirmed = bool(result)
    emit_trace(config, state, "confirm", "confirm_result", {"confirmed": confirmed})

    # Clear interrupt state
    return {
        "pending_interrupt": None,
        "interrupt_message": None,
        "interrupt_result": confirmed,
        "pending_execute": state.get("pending_execute"),
        # If not confirmed, end task
        "finished": not confirmed,
        "action_result": (
            {
                "success": False,
                "should_finish": not confirmed,
                "message": (
                    "User cancelled sensitive operation" if not confirmed else None
                ),
            }
            if not confirmed
            else None
        ),
    }
