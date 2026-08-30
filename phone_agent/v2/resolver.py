"""Target resolution: description -> unique mark, fail-closed.

Per refactor-thin-loop-v2.md §8. The resolver never taps raw coordinates; it
maps a natural-language description to exactly one ``MarkCandidate`` from the
session's current marks, falling back to deep visual localization
(``session.locate``) only when the current marks yield zero text matches.

Matching precedence over the current marks (``session.marks``):
    1. exact match on ``text_summary`` or ``role``
    2. substring match (description contained in mark text/role)
    3. normalized fuzzy match (whitespace stripped, case-folded)

The first tier with any hits wins. Within that tier a single hit is returned;
multiple hits raise :class:`ResolveAmbiguousError` (fail-closed, never guess).
Zero hits across all tiers delegate to ``session.locate`` (LocateAnything).
"""

from __future__ import annotations

from phone_agent.grounding.provider import MarkCandidate

# Session-owned exceptions live in ``phone_agent.v2.session`` (core worktree).
# Import defensively so the tools layer and unit tests work before session.py
# lands; integration binds the real classes.
try:  # pragma: no cover - exercised via integration
    from phone_agent.v2.session import LocateAmbiguousError, StaleMarkError
except Exception:  # noqa: BLE001 - session.py not yet present in this worktree

    class StaleMarkError(Exception):
        """Raised when a mark_id is no longer part of the current screen."""

    class LocateAmbiguousError(Exception):
        """Raised when deep localization yields zero or multiple candidates."""


class ResolveAmbiguousError(Exception):
    """Raised when a description matches multiple current marks.

    Carries up to five human-readable candidate summaries so the tool layer can
    surface them to the model without executing anything.
    """

    def __init__(self, description: str, candidates: list[str]) -> None:
        self.description = description
        self.candidates = candidates[:5]
        summary = " · ".join(self.candidates)
        super().__init__(
            f"description {description!r} matched {len(candidates)} marks: {summary}"
        )


def _normalize(text: str | None) -> str:
    """Case-fold and strip all whitespace for fuzzy comparison."""

    if not text:
        return ""
    return "".join(str(text).split()).casefold()


def candidate_summary(mark: MarkCandidate) -> str:
    """Compact ``mark_id|role|text`` summary for candidate lists."""

    role = mark.role or "?"
    text = (mark.text_summary or "").strip()
    if len(text) > 32:
        text = text[:31] + "…"
    return f"{mark.mark_id}|{role}|{text}"


def _fields(mark: MarkCandidate) -> tuple[str, str]:
    return (mark.text_summary or ""), (mark.role or "")


def resolve_description(session, description: str) -> MarkCandidate:
    """Resolve a description to exactly one mark, fail-closed.

    Raises :class:`ResolveAmbiguousError` on multiple current-mark hits and
    propagates :class:`LocateAmbiguousError` from ``session.locate`` so the tool
    layer can render a single "ambiguous" message and refuse to execute.
    """

    query = (description or "").strip()
    marks = list(getattr(session, "marks", {}).values())

    exact: list[MarkCandidate] = []
    substring: list[MarkCandidate] = []
    fuzzy: list[MarkCandidate] = []

    q_norm = _normalize(query)
    for mark in marks:
        text, role = _fields(mark)
        if query and (text == query or role == query):
            exact.append(mark)
            continue
        if query and (query in text or query in role):
            substring.append(mark)
            continue
        t_norm, r_norm = _normalize(text), _normalize(role)
        if q_norm and (q_norm in t_norm or q_norm in r_norm):
            fuzzy.append(mark)

    for tier in (exact, substring, fuzzy):
        if len(tier) == 1:
            return tier[0]
        if len(tier) > 1:
            raise ResolveAmbiguousError(
                query, [candidate_summary(m) for m in tier]
            )

    # Zero current-mark hits -> deep visual fallback (may raise
    # LocateAmbiguousError, which the tool layer catches).
    return session.locate(query)
