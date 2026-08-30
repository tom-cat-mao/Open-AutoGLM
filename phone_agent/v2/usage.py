"""Per-run token usage ledger shared by actor and side-model calls."""

from __future__ import annotations

from threading import Lock
from typing import Any

from phone_agent.v2.middleware._tokens import estimate_message_tokens, usage_tokens

USAGE_ROLES = frozenset({"actor", "compact", "verifier", "reviewer", "distill"})


class UsageLedger:
    """Thread-safe-enough token accumulator for one agent run.

    Provider-reported usage wins whenever it is available. Callers may supply a
    fuller request-plus-response estimate for providers that omit metadata; when
    they do not, the response message itself is estimated as a final fallback.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._total = 0
        self._by_role: dict[str, int] = {}

    def record(
        self,
        role: str,
        message_or_none: Any = None,
        *,
        estimate_tokens: int | None = None,
    ) -> int:
        """Record one model call and return the number of tokens counted."""

        if role not in USAGE_ROLES:
            raise ValueError(f"unknown usage role: {role!r}")

        reported = (
            usage_tokens(message_or_none) if message_or_none is not None else None
        )
        if reported is not None:
            counted = reported
        elif estimate_tokens is not None:
            counted = int(estimate_tokens)
        elif message_or_none is not None:
            counted = estimate_message_tokens(message_or_none)
        else:
            counted = 0
        counted = max(0, counted)

        with self._lock:
            self._total += counted
            self._by_role[role] = self._by_role.get(role, 0) + counted
        return counted

    @property
    def total(self) -> int:
        """Grand total across actor and every side-model role."""

        with self._lock:
            return self._total

    def by_role(self) -> dict[str, int]:
        """Return a snapshot of cumulative usage grouped by model role."""

        with self._lock:
            return dict(self._by_role)

    def reset(self) -> None:
        """Clear all per-run totals."""

        with self._lock:
            self._total = 0
            self._by_role.clear()


__all__ = ["UsageLedger", "USAGE_ROLES"]
