"""Human-in-the-Loop takeover node using LangGraph interrupt().

Replaces ActionHandler.takeover_callback with a resumable interrupt.
"""

from typing import TYPE_CHECKING
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from phone_agent.graph.trace import emit_trace

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def takeover_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Takeover node: pause graph execution and request user takeover (login, captcha, etc.).

    Uses LangGraph interrupt() to pause the graph. On resume, execution
    continues after the user completes the manual operation.
    """
    message = state.get("interrupt_message") or "User intervention required"
    emit_trace(config, state, "takeover", "takeover_interrupt", {"message": message})
    # interrupt() raises GraphInterrupt on first call, returns resume value on second
    interrupt(
        {
            "type": "takeover",
            "message": message,
            "prompt": f"{message}\nPress Enter after completing manual operation...",
        }
    )
    emit_trace(config, state, "takeover", "takeover_result", {"completed": True})
    # Clear interrupt state
    # H2 Fix E: also clear interrupt_result so a stale resume value can never
    # leak into the next execute pass (add-only: existing keys untouched).
    return {
        "pending_interrupt": None,
        "interrupt_message": None,
        "interrupt_result": None,
    }
