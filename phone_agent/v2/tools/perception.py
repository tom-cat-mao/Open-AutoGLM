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

    def read_screen(
        intent: str = "",
        note: str | None = None,
        settle_ms: int | None = None,
    ) -> list[dict]:
        """Re-observe the current screen (no side effects on the device).

        Returns the current app and a marks digest so you can pick a
        ``target_mark_id`` for the next action, plus a fresh screenshot image
        when the screen changed since the last one sent.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        ``settle_ms`` replaces the global observation delay.
        搜索/提交/打开页面后建议 1500-2500ms；普通点击留空。
        """

        mark_tool_ok(session)
        return auto_observation(session, settle_ms=settle_ms)

    def locate(
        description: str,
        intent: str = "",
        note: str | None = None,
        visible_text_hint: str | None = None,
        scope_mark_id: str | None = None,
        scope_start_mark_id: str | None = None,
        scope_end_mark_id: str | None = None,
    ) -> tuple[str | list[dict], dict]:
        """Deep visual localization for a target accessibility marks miss.

        On success the located element is registered as a new mark, tappable via
        ``tap(target_mark_id=...)``. Ambiguous/failed localization returns the
        candidates or the failure reason and registers nothing.
        ``visible_text_hint`` is exact text on or near the target. Optionally
        narrow the search with one container ``scope_mark_id``, or an interval
        from ``scope_start_mark_id`` to ``scope_end_mark_id``.

        Always pass ``intent`` (this step's goal). ``note`` optionally records
        what you discovered this step.
        """

        try:
            mark = session.locate(
                description,
                visible_text_hint=visible_text_hint,
                intent=intent,
                scope_mark_id=scope_mark_id,
                scope_start_mark_id=scope_start_mark_id,
                scope_end_mark_id=scope_end_mark_id,
            )
        except LocateAmbiguousError as exc:
            mark_tool_fail(session)
            if getattr(exc, "failure_code", "no_candidate") == "ambiguous":
                content = (
                    f"未定位: {exc}（可用 scope 收紧区域，或补充更独特的可见文字）"
                )
            else:
                content = (
                    f"未定位: {exc}（可补充目标外观/可见文字/相对位置，"
                    "或用 scope 圈定区域后重试）"
                )
            return content, _locate_artifact(session)
        except Exception as exc:  # noqa: BLE001 - surface provider failure text
            mark_tool_fail(session)
            return f"定位失败: {exc}", _locate_artifact(session)
        mark_tool_ok(session)
        head = (
            f"已定位并注册为 mark {mark.mark_id}，可用 "
            f"tap(target_mark_id={mark.mark_id!r}) 点击 [{candidate_summary(mark)}]"
        )
        # U1: return the same frame the visual model located on (no extra observe).
        return locate_observation(session, head), _locate_artifact(session)

    return [
        StructuredTool.from_function(read_screen, parse_docstring=True),
        StructuredTool.from_function(
            locate, parse_docstring=True, response_format="content_and_artifact"
        ),
    ]


def _locate_artifact(session) -> dict:
    """Trace-safe locate metadata; never includes screenshot pixels/base64."""

    getter = getattr(session, "last_locate_metadata", None)
    if not callable(getter):
        return {}
    try:
        value = getter()
    except Exception:  # noqa: BLE001 - metadata must never break the tool result
        return {}
    return value if isinstance(value, dict) else {}
