"""U1 observation lifecycle: atomic single producer + batch-badge freshness gate.

Covers the U1 contract (design-locked):
    - atomic ``observe()`` produces one consistent frame (screenshot + marks +
      foreground taken in one window), ``refresh_marks`` reuses that shot;
    - batch badges: external mark ids carry an ``@e<epoch>`` suffix that never
      repeats across observations;
    - freshness gate: a badged id from a superseded batch fails closed in
      ``resolve_mark`` (StaleMarkError) and the tool never executes;
    - observation failure invalidates the whole batch (marks cleared);
    - ``locate`` mints the resolved mark into the current batch and the locate
      tool returns the same frame (no extra ``observe``).

These tests use a fake DeviceFactory so no real device / MLX / network is hit.
"""

from __future__ import annotations

import pytest

from phone_agent.grounding.provider import MarkCandidate
from phone_agent.v2.session import (
    StaleMarkError,
    mint_badge,
    parse_badge,
)


def test_badge_roundtrip():
    badged = mint_badge("ax_3", 7)
    assert badged == "ax_3@e7"
    base, epoch = parse_badge(badged)
    assert base == "ax_3"
    assert epoch == 7


def test_parse_badge_unbadged_returns_none():
    base, epoch = parse_badge("ax_3")
    assert base == "ax_3"
    assert epoch is None


def test_resolve_mark_rejects_stale_batch():
    """A badged id from an older batch is rejected before the marks lookup."""

    from phone_agent.v2.session import PhoneSession

    session = PhoneSession.__new__(PhoneSession)
    session.epoch = 5
    session.marks = {
        "ax_1@e5": MarkCandidate(
            mark_id="ax_1@e5", bbox=[0, 0, 10, 10], center=[5, 5], epoch=5
        )
    }
    # current-batch id resolves
    assert session.resolve_mark("ax_1@e5").mark_id == "ax_1@e5"
    # a stale-batch id fails closed even though the provider id "ax_1" is live
    with pytest.raises(StaleMarkError):
        session.resolve_mark("ax_1@e4")
