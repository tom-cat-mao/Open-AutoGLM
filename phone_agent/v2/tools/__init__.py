"""v2 tool assembly.

``build_tools(session, config)`` returns the full LangChain tool list the thin
agent is created with: actuation + perception + control (§7).
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from phone_agent.v2.tools.actuation import build_actuation_tools
from phone_agent.v2.tools.control import build_control_tools
from phone_agent.v2.tools.perception import build_perception_tools
from phone_agent.v2.tools.taskdoc import make_update_task_doc_tool


def build_tools(session, config) -> list[BaseTool]:
    """Assemble every v2 tool bound to ``session``/``config``."""

    tools: list[BaseTool] = [
        *build_perception_tools(session, config),
        *build_actuation_tools(session, config),
        *build_control_tools(session, config),
    ]
    if getattr(config, "taskdoc_enabled", True):
        tools.append(
            make_update_task_doc_tool(session, getattr(config, "lang", "cn"))
        )
    return tools


__all__ = ["build_tools"]
