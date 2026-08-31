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


class _FinishOffConfig:
    def __init__(self, config) -> None:
        self._config = config

    @property
    def finish_verify(self) -> str:
        return "off"

    def __getattr__(self, name: str):
        return getattr(self._config, name)


def build_base_tools(session, config) -> list[BaseTool]:
    """Build the always-present tool table before capability mounts.

    The ``finish`` tool here is the legacy/off-mode floor.  When the
    ``finish_verify`` capability is active it mounts a same-named configured
    tool, and the assembly context replaces this entry in place.
    """

    return [
        *build_perception_tools(session, config),
        *build_actuation_tools(session, config),
        *build_control_tools(session, _FinishOffConfig(config)),
    ]


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


__all__ = ["build_base_tools", "build_tools"]
