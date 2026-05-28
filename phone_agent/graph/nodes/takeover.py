"""Human-in-the-Loop takeover node using LangGraph interrupt().

Replaces ActionHandler.takeover_callback with a resumable interrupt.
"""

from typing import TYPE_CHECKING
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def takeover_node(state: "AgentState", config: RunnableConfig) -> dict:
    """
    Takeover node: pause graph execution and request user takeover (login, captcha, etc.).

    Uses LangGraph interrupt() to pause the graph. On resume, execution
    continues after the user completes the manual operation.
    """
    message = state.get("interrupt_message") or "User intervention required"
    # interrupt() raises GraphInterrupt on first call, returns resume value on second
    interrupt(
        {
            "type": "takeover",
            "message": message,
            "prompt": f"{message}\nPress Enter after completing manual operation...",
        }
    )
    # Clear interrupt state
    return {
        "pending_interrupt": None,
        "interrupt_message": None,
    }
