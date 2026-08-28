"""Budget-warn middleware: L0 mirror of the remaining model-call budget (S1 §3.1).

Per S1 §3.1 this is a pure **L0 镜子** — it only reflects world state (how much
budget is left plus the option space), it never instructs and never stops. Once
the thread model-call count reaches ``ceil(warn_ratio * limit)`` it injects a
single ``SystemMessage`` (once per run, guarded by ``self._warned``) noting the
remaining budget and the option space; the hard stop stays with
``ModelCallLimitMiddleware``.

The count is read from ``state["thread_model_call_count"]`` (S1 F4: any custom
middleware can read the ModelCallLimit merged state in ``before_model``). It may
be absent on the very first turn, so ``.get(..., 0)`` tolerates that.
"""

from __future__ import annotations

import math
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

_WARN_TEXT = {
    "cn": (
        "预算余量：已用 {count}/{limit} 次模型调用，剩 {remaining} 次。"
        "若任务已达成请立即 finish（附 evidence）；否则用 update_task_doc "
        "收敛路线，优先做关键项。"
    ),
    "en": (
        "Budget remaining: {count}/{limit} model calls used, {remaining} left. "
        "If the task is done call finish (with evidence); otherwise use "
        "update_task_doc to converge the route and do the critical items first."
    ),
}


class BudgetMiddleware(AgentMiddleware):
    """before_model: inject a one-time budget-remaining mirror at ``warn_ratio``."""

    def __init__(
        self, max_model_calls: int = 20, warn_ratio: float = 0.8, lang: str = "cn"
    ) -> None:
        super().__init__()
        self.limit = max(1, int(max_model_calls))
        # Clamp to (0, 1]: an out-of-range ratio must not disable or over-fire.
        ratio = float(warn_ratio)
        if ratio <= 0 or ratio > 1:
            ratio = 0.8
        self.warn_ratio = ratio
        self.lang = lang
        self._threshold = max(1, math.ceil(self.warn_ratio * self.limit))
        self._warned = False

    def reset(self) -> None:
        """Clear the one-shot guard so a reused agent warns again on the next run."""

        self._warned = False

    def _warn_text(self, count: int) -> str:
        template = _WARN_TEXT.get(self.lang, _WARN_TEXT["cn"])
        return template.format(
            count=count, limit=self.limit, remaining=max(0, self.limit - count)
        )

    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        if self._warned:
            return None
        count = 0
        if isinstance(state, dict):
            count = int(state.get("thread_model_call_count", 0) or 0)
        if count < self._threshold:
            return None
        self._warned = True
        return {"messages": [SystemMessage(content=self._warn_text(count))]}

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)


def build_budget_middleware(
    max_model_calls: int = 20, warn_ratio: float = 0.8, lang: str = "cn"
) -> BudgetMiddleware:
    """Build a :class:`BudgetMiddleware` from the resolved config values."""

    return BudgetMiddleware(
        max_model_calls=max_model_calls, warn_ratio=warn_ratio, lang=lang
    )


__all__ = ["BudgetMiddleware", "build_budget_middleware"]
