"""Perception tools: read_screen / locate.

Per refactor-thin-loop-v2.md §7.2. ``read_screen`` is a side-effect-free
re-observation; ``locate`` runs deep visual localization (LocateAnything) when
accessibility marks miss the target, registering the resolved mark on success.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from phone_agent.v2.resolver import LocateAmbiguousError, candidate_summary
from phone_agent.v2.tools._obs import (
    auto_observation,
    locate_observation,
    mark_tool_fail,
    mark_tool_ok,
)


def build_perception_tools(session, config) -> list[StructuredTool]:
    """Return the perception tool list bound to ``session``."""

    def read_screen() -> list[dict]:
        """Re-observe the current screen (no side effects on the device).

        Returns the current app and a marks digest so you can pick a
        ``target_mark_id`` for the next action, plus a fresh screenshot image
        when the screen changed since the last one sent.
        """

        mark_tool_ok(session)
        return auto_observation(session)

    def locate(description: str) -> str | list[dict]:
        """Deep visual localization for a target accessibility marks miss.

        On success the located element is registered as a new mark, tappable via
        ``tap(target_mark_id=...)``. Ambiguous/failed localization returns the
        candidates or the failure reason and registers nothing.
        """

        try:
            mark = session.locate(description)
        except LocateAmbiguousError as exc:
            mark_tool_fail(session)
            return f"未定位: {exc}（请细化描述）"
        except Exception as exc:  # noqa: BLE001 - surface provider failure text
            mark_tool_fail(session)
            return f"定位失败: {exc}"
        mark_tool_ok(session)
        head = (
            f"已定位并注册为 mark {mark.mark_id}，可用 "
            f"tap(target_mark_id={mark.mark_id!r}) 点击 [{candidate_summary(mark)}]"
        )
        # U1: return the same frame the visual model located on (no extra observe).
        return locate_observation(session, head)

    return [
        StructuredTool.from_function(read_screen, parse_docstring=True),
        StructuredTool.from_function(locate, parse_docstring=True),
    ]
