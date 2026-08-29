"""Budget middleware: token-cost mirror + hard cost ceiling (S1 §3.1 / A4 §2).

A4 re-bases the run budget from *model-call count* to *token cost* — the gateway
bills per token, so tokens are the meaningful ceiling. This middleware owns the
**two-level token budget**:

* **L0 warn (mirror, never stops)** — once the remaining budget drops to
  ``warn_remaining`` it injects a single ``SystemMessage`` (once per run) that
  mirrors the remaining tokens and the full option space (finish / write key
  facts into the TaskDoc / converge the route / take_over). Pure information.
* **Hard cost ceiling (stops once)** — once cumulative usage reaches
  ``token_budget`` it jumps the graph to ``end`` with a ``[TOKEN_BUDGET_EXHAUSTED]``
  marker. This is the cost cap; ``ModelCallLimitMiddleware`` (``PHONE_AGENT_MAX_STEPS``,
  A4 default 100) is now only an independent **runaway-loop fuse**.

Cumulative accounting is compaction-proof: the auto-compact middleware replaces
old ``AIMessage``s (and their ``usage_metadata``) with a summary, so re-summing
the live transcript would *undercount* the cost already billed. With a shared
``UsageLedger``, actor turns and side-model calls contribute to one per-run total;
without one, the original private actor counter remains the compatibility path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, SystemMessage

from phone_agent.v2.middleware._tokens import estimate_message_tokens, usage_tokens

if TYPE_CHECKING:
    from phone_agent.v2.usage import UsageLedger

# Terminal marker text the hard ceiling injects; agent._build_result keys on the
# instance flag, not this string, but it keeps the transcript self-describing.
TOKEN_BUDGET_EXHAUSTED_MARKER = "[TOKEN_BUDGET_EXHAUSTED]"

_WARN_TEXT = {
    "cn": (
        "Token 预算余量：已用约 {used}/{budget}，剩约 {remaining}。"
        "若任务已达成请立即 finish（附 evidence）；否则可用 update_task_doc "
        "把要紧事实写进任务板（压缩免疫）、收敛路线并优先做关键项；"
        "若需人工介入请 take_over。"
    ),
    "en": (
        "Token budget remaining: ~{used}/{budget} used, ~{remaining} left. "
        "If the task is done call finish (with evidence); otherwise use "
        "update_task_doc to record key facts into the board (compaction-immune), "
        "converge the route and do critical items first; take_over if a human is "
        "needed."
    ),
}

_EXHAUSTED_TEXT = {
    "cn": (
        TOKEN_BUDGET_EXHAUSTED_MARKER
        + " Token 预算已耗尽（约 {used}/{budget}），本次运行结束。"
    ),
    "en": (
        TOKEN_BUDGET_EXHAUSTED_MARKER
        + " Token budget exhausted (~{used}/{budget}); ending this run."
    ),
}


class BudgetMiddleware(AgentMiddleware):
    """Token-cost mirror (warn) + hard ceiling (stop); cumulative & compaction-proof."""

    def __init__(
        self,
        token_budget: int = 1_000_000,
        warn_remaining: int = 100_000,
        lang: str = "cn",
        ledger: UsageLedger | None = None,
    ) -> None:
        super().__init__()
        self.token_budget = max(1, int(token_budget))
        # Clamp the absolute warn line into (0, token_budget]; an out-of-range
        # value must neither disable the warn nor fire it before any usage.
        warn = int(warn_remaining)
        if warn <= 0 or warn > self.token_budget:
            warn = min(100_000, self.token_budget)
        self.warn_remaining = warn
        self.lang = lang
        self.ledger = ledger
        self._warned = False
        self._exhausted = False
        self._used_tokens = 0
        self._counted_id: str | None = None

    def reset(self) -> None:
        """Clear per-run state so a reused agent budgets the next run from zero."""

        self._warned = False
        self._exhausted = False
        self._used_tokens = 0
        self._counted_id = None
        if self.ledger is not None:
            self.ledger.reset()

    @property
    def used_tokens(self) -> int:
        """Cumulative billed tokens accounted so far (compaction-proof)."""

        if self.ledger is not None:
            return self.ledger.total
        return self._used_tokens

    @property
    def exhausted(self) -> bool:
        """True once the hard cost ceiling fired this run."""

        return self._exhausted

    # ------------------------------------------------------------------
    def _newest_ai(self, messages: list[Any]) -> AIMessage | None:
        for msg in reversed(messages or []):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _accumulate(self, messages: list[Any]) -> None:
        """Add the newest AI turn's usage to the cumulative counter (once)."""

        newest = self._newest_ai(messages)
        if newest is None:
            return
        msg_id = getattr(newest, "id", None)
        # Only count a given AI message once. When ids are absent (rare), fall back
        # to always counting the newest — after_model runs once per model call.
        if msg_id is not None and msg_id == self._counted_id:
            return
        estimate = estimate_message_tokens(newest)
        if self.ledger is not None:
            self.ledger.record("actor", newest, estimate_tokens=estimate)
        else:
            reported = usage_tokens(newest)
            self._used_tokens += reported if reported is not None else estimate
        self._counted_id = msg_id

    def _warn_text(self) -> str:
        template = _WARN_TEXT.get(self.lang, _WARN_TEXT["cn"])
        used = self.used_tokens
        remaining = max(0, self.token_budget - used)
        return template.format(
            used=used, budget=self.token_budget, remaining=remaining
        )

    def _exhausted_text(self) -> str:
        template = _EXHAUSTED_TEXT.get(self.lang, _EXHAUSTED_TEXT["cn"])
        return template.format(used=self.used_tokens, budget=self.token_budget)

    # ------------------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        remaining = self.token_budget - self.used_tokens
        # Hard cost ceiling: stop the run once the budget is fully spent.
        if remaining <= 0:
            if not self._exhausted:
                self._exhausted = True
                return {
                    "jump_to": "end",
                    "messages": [AIMessage(content=self._exhausted_text())],
                }
            return {"jump_to": "end"}
        # L0 warn mirror (once per run) as the remaining budget crosses the line.
        if not self._warned and remaining <= self.warn_remaining:
            self._warned = True
            return {"messages": [SystemMessage(content=self._warn_text())]}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)

    def after_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        messages = state.get("messages") if isinstance(state, dict) else None
        self._accumulate(messages or [])
        return None

    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.after_model(state, runtime)


def build_budget_middleware(
    token_budget: int = 1_000_000,
    warn_remaining: int = 100_000,
    lang: str = "cn",
    ledger: UsageLedger | None = None,
) -> BudgetMiddleware:
    """Build a :class:`BudgetMiddleware` from the resolved config values."""

    return BudgetMiddleware(
        token_budget=token_budget,
        warn_remaining=warn_remaining,
        lang=lang,
        ledger=ledger,
    )


__all__ = [
    "BudgetMiddleware",
    "build_budget_middleware",
    "TOKEN_BUDGET_EXHAUSTED_MARKER",
]
