"""TaskDoc middleware: pin the task board into context + stagnation nudge.

Per ``docs/refactor-thin-loop-v2-taskdoc.md`` §2.3. This middleware is the
render hook of the TaskDoc increment: before every model call it re-injects the
task board (goal + route + key facts) as a single ``[TASK_DOC]`` system message
kept at the tail of the transcript. Because the block is re-emitted each turn
(old copy removed, fresh copy appended), it is *pinned* and *compression-immune*
— the image-pruning / future-compaction passes never strip the model's view of
what it is trying to accomplish.

Stagnation nudge (design principle: **non-directive**): when the device has not
produced a new observed state for ``config.taskdoc_nudge_steps`` consecutive
model calls and the task board still has open items, a single hint is appended
after the render block. It only *states the observation and the option space*
(``update_task_doc`` / ``locate`` / ``ask_user`` / ``take_over`` / ``finish``);
it never issues an instruction, and it fires at most once per run
(``session.nudged``).
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import RemoveMessage, SystemMessage

# Stable prefix for the pinned task-board id so the block is easy to spot in a
# transcript; the suffix is per-injection unique so add_messages re-appends the
# refreshed copy at the tail instead of replacing it in place.
_TASKDOC_ID_PREFIX = "__taskdoc__"

_NUDGE_TEXT = {
    "cn": (
        "最近 {n} 步无新状态。可选：update_task_doc 修正路线 / locate / "
        "ask_user / take_over / finish（若已完成）"
    ),
    "en": (
        "No new state in the last {n} steps. Options: update_task_doc to revise "
        "the route / locate / ask_user / take_over / finish (if already done)"
    ),
}


class TaskDocMiddleware(AgentMiddleware):
    """before_model: inject the rendered TaskDoc block + stagnation nudge."""

    def __init__(self, session: Any, lang: str = "cn", nudge_steps: int = 5) -> None:
        super().__init__()
        self.session = session
        self.lang = lang
        self.nudge_steps = max(1, int(nudge_steps))
        # Highest number of distinct observed states seen so far this run and the
        # count of consecutive stagnant (no-growth) before_model calls.
        self._max_seen: int = 0
        self._stagnant: int = 0
        # Id of the last injected task-board message (removed + refreshed next turn).
        self._injected_id: str | None = None

    # ------------------------------------------------------------------
    def _nudge_text(self, steps: int) -> str:
        template = _NUDGE_TEXT.get(self.lang, _NUDGE_TEXT["cn"])
        return template.format(n=steps)

    def _should_nudge(self, has_open_items: bool) -> bool:
        """Advance the stagnation counter and decide whether to nudge this turn.

        A model call is *stagnant* when ``session.seen_states`` did not grow
        beyond the highest count seen so far. After ``nudge_steps`` consecutive
        stagnant calls, nudge once per run (guarded by ``session.nudged``).
        """

        seen = getattr(self.session, "seen_states", None)
        count = len(seen) if seen is not None else 0
        if count > self._max_seen:
            self._max_seen = count
            self._stagnant = 0
        else:
            self._stagnant += 1

        if getattr(self.session, "nudged", False):
            return False
        if not has_open_items:
            return False
        return self._stagnant >= self.nudge_steps

    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        doc = getattr(self.session, "task_doc", None)
        if doc is None:
            return None

        try:
            rendered = doc.render(self.lang)
        except Exception:  # noqa: BLE001 - a broken render must not crash the loop
            return None
        if not rendered:
            # Empty document: nothing pinned, and nothing to nudge alongside.
            return None

        has_open = False
        try:
            has_open = bool(doc.has_open_items())
        except Exception:  # noqa: BLE001 - treat a broken predicate as no open items
            has_open = False

        block = "[TASK_DOC]\n" + rendered
        if self._should_nudge(has_open):
            block = block + "\n\n" + self._nudge_text(self._stagnant)
            try:
                self.session.nudged = True
            except Exception:  # noqa: BLE001 - best-effort flag write
                pass

        new_id = f"{_TASKDOC_ID_PREFIX}{uuid.uuid4().hex}"
        out: list[Any] = []
        if self._injected_id is not None:
            # Drop the previous pinned copy so exactly one block exists, refreshed
            # at the tail (RemoveMessage on the stale id, then append the new one).
            out.append(RemoveMessage(id=self._injected_id))
        out.append(SystemMessage(content=block, id=new_id))
        self._injected_id = new_id
        return {"messages": out}

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)


def build_taskdoc_middleware(
    session: Any, lang: str = "cn", nudge_steps: int = 5
) -> TaskDocMiddleware:
    """Build a :class:`TaskDocMiddleware` bound to ``session``.

    The session is captured directly so the middleware can read
    ``session.task_doc`` / ``seen_states`` / ``nudged`` before every model call.
    """

    return TaskDocMiddleware(session, lang=lang, nudge_steps=nudge_steps)


__all__ = ["TaskDocMiddleware", "build_taskdoc_middleware"]
