"""Tests for rule-based App-KB dream consolidation."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from phone_agent.v2.appkb import AppKnowledgeStore
from phone_agent.v2.dream import consolidate


def _entry(
    term: str,
    package: str,
    *,
    label: str,
    kind: str = "learned",
    scope: str = "global",
    confidence: float,
    success_count: int,
    first_seen: str,
    last_seen: str,
    stale: bool = False,
) -> dict[str, object]:
    return {
        "term": term,
        "label": label,
        "package": package,
        "kind": kind,
        "scope": scope,
        "confidence": confidence,
        "success_count": success_count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "stale": stale,
    }


def test_consolidate_merges_reconciles_prunes_and_compacts(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    old = "2025-01-01T00:00:00+00:00"
    recent = "2026-06-01T00:00:00+00:00"
    store.upsert(
        _entry(
            "Maps",
            "com.example.maps",
            label="Maps",
            confidence=0.6,
            success_count=2,
            first_seen=old,
            last_seen="2026-05-01T00:00:00+00:00",
        )
    )
    store.upsert(
        _entry(
            "Maps app",
            "com.example.maps",
            label="Maps",
            kind="user",
            confidence=0.9,
            success_count=5,
            first_seen="2025-06-01T00:00:00+00:00",
            last_seen=recent,
        )
    )
    store.upsert(
        _entry(
            "Gone",
            "com.example.gone",
            label="Gone",
            kind="device",
            scope="device:one",
            confidence=0.4,
            success_count=0,
            first_seen=recent,
            last_seen=recent,
        )
    )
    store.upsert(
        _entry(
            "Old",
            "com.example.old",
            label="Old",
            confidence=0.3,
            success_count=0,
            first_seen=old,
            last_seen=old,
        )
    )
    store.upsert(
        _entry(
            "Trusted old",
            "com.example.trusted",
            label="Trusted old",
            confidence=0.8,
            success_count=8,
            first_seen=old,
            last_seen=old,
        )
    )

    result = consolidate(
        store,
        inventory={"com.example.maps"},
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        max_age_days=90,
    )

    assert result == {"merged": 1, "staled": 1, "deleted": 2, "kept": 2}
    surviving = store.entries(include_stale=True)
    assert {entry["package"] for entry in surviving} == {
        "com.example.maps",
        "com.example.trusted",
    }
    merged = next(
        entry for entry in surviving if entry["package"] == "com.example.maps"
    )
    assert merged["confidence"] == 0.9
    assert merged["success_count"] == 7
    assert merged["first_seen"] == old
    assert merged["last_seen"] == recent

    event_lines = store.events_path.read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == result["kept"]
    assert all(json.loads(line)["op"] == "upsert" for line in event_lines)
    assert json.loads(store.kb_path.read_text(encoding="utf-8")) == surviving
    assert AppKnowledgeStore(str(tmp_path)).entries(include_stale=True) == surviving


def test_consolidate_keeps_high_confidence_stale_entry(tmp_path):
    store = AppKnowledgeStore(str(tmp_path))
    store.upsert(
        _entry(
            "Missing",
            "com.example.missing",
            label="Missing",
            kind="device",
            scope="device:one",
            confidence=0.9,
            success_count=3,
            first_seen="2026-01-01T00:00:00+00:00",
            last_seen="2026-06-01T00:00:00+00:00",
        )
    )

    result = consolidate(
        store,
        inventory=set(),
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert result == {"merged": 0, "staled": 1, "deleted": 0, "kept": 1}
    assert store.entries(include_stale=True)[0]["stale"] is True
    assert store.entries() == []
