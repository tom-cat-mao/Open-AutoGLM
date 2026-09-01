"""Target resolution: description -> unique mark, fail-closed.

Per AGENTS.md. The resolver never taps raw coordinates; it
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

from collections.abc import Mapping
from typing import Any

from phone_agent.config.apps import (
    DEFAULT_APP_REGISTRY,
    DEFAULT_LAUNCH_TARGET_RESOLVER,
)
from phone_agent.grounding.provider import MarkCandidate
from phone_agent.v2.names import (
    AppNameResolution,
    ResolverSettings,
    embedding_search_from_config,
    resolve_name,
)

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


def _app_kb_entries(session: Any) -> list[Mapping[str, Any]]:
    """Return applicable resolver rows, degrading old doubles to snapshots."""

    knowledge = getattr(session, "app_knowledge", None)
    entries = getattr(knowledge, "entries", None)
    if callable(entries):
        try:
            return list(entries())
        except Exception:  # noqa: BLE001 - optional KB view is fail-open
            pass
    snapshot = getattr(knowledge, "snapshot", None)
    if callable(snapshot):
        try:
            return [
                {
                    "term": str(term),
                    "label": str(term),
                    "package": str(package),
                    "kind": "learned",
                    "success_count": 0,
                }
                for term, package in snapshot().items()
            ]
        except Exception:  # noqa: BLE001 - optional KB view is fail-open
            return []
    return []


def resolve_app_name(
    session: Any,
    config: Any,
    mention: str,
    *,
    inventory: Any | None = None,
    registry: Any = DEFAULT_APP_REGISTRY,
    embedding_search=None,  # noqa: ANN001 - protocol lives in names.py
) -> AppNameResolution:
    """Resolve an app mention through the shared four-route name core.

    Installed packages are represented as device-prior sources. This function
    still returns only a name decision; callers must separately apply launch
    policy and installation checks to its winning package.
    """

    entries = list(_app_kb_entries(session))
    for package in sorted(getattr(inventory, "packages", ()) or ()):
        entries.append(
            {
                "term": str(package),
                "label": str(package),
                "package": str(package),
                "kind": "device",
                "success_count": 0,
            }
        )
    if embedding_search is None:
        cached = getattr(session, "_resolver_embedding_search", None)
        if callable(cached):
            embedding_search = cached
        scope = getattr(inventory, "device_id", None)
        if not scope:
            kb_device_id = getattr(session, "_kb_device_id", None)
            try:
                scope = kb_device_id() if callable(kb_device_id) else None
            except Exception:  # noqa: BLE001 - embedding route is optional
                scope = None
        if scope and embedding_search is None:
            embedding_search = embedding_search_from_config(
                config, device_scope=f"device:{scope}"
            )
            if embedding_search is not None:
                try:
                    session._resolver_embedding_search = embedding_search
                except Exception:  # noqa: BLE001 - cache is only an optimization
                    pass
    return resolve_name(
        mention,
        registry=registry,
        kb_entries=entries,
        embedding_search=embedding_search,
        settings=ResolverSettings.from_config(config),
    )


def authorize_app_candidate(
    package: str,
    *,
    inventory: Any | None,
    resolver: Any = DEFAULT_LAUNCH_TARGET_RESOLVER,
):
    """Apply the existing install + launch-policy boundary to one package."""

    return resolver.resolve(
        package,
        inventory=inventory,
        candidates=[package],
    )
