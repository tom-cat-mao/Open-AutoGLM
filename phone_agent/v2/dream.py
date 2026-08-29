"""Rule-based App-KB consolidation with no model or device dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from phone_agent.v2.appkb import AppKnowledgeStore, _parse_iso, _sort_key


def consolidate(
    store: AppKnowledgeStore,
    *,
    inventory: set[str] | None = None,
    now: datetime | None = None,
    max_age_days: int = 90,
    light: bool = False,
) -> dict[str, int]:
    """Merge, reconcile, prune, and compact one App-KB store.

    Device inventory, when supplied, is authoritative for device entries.
    Deletion remains conservative: only stale or aged entries below 0.5
    confidence are pruned. ``light=True`` performs only merge + reconciliation
    for automatic post-run maintenance and never deletes an entry.
    """

    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    current_time = _as_utc(now or datetime.now(timezone.utc))
    entries = store.entries(include_stale=True)

    merged_entries, merged_count = _merge_duplicates(entries)

    staled_count = 0
    if inventory is not None:
        for entry in merged_entries:
            if (
                entry["kind"] == "device"
                and entry["package"] not in inventory
                and not entry["stale"]
            ):
                entry["stale"] = True
                staled_count += 1

    kept: list[dict[str, Any]] = []
    deleted_count = 0
    if light:
        kept = merged_entries
    else:
        cutoff = current_time - timedelta(days=max_age_days)
        for entry in merged_entries:
            unused = _parse_iso(entry["last_seen"]) < cutoff
            if (entry["stale"] or unused) and entry["confidence"] < 0.5:
                deleted_count += 1
                continue
            kept.append(entry)

    kept.sort(key=_sort_key)
    store._compact(kept, timestamp=current_time.isoformat())
    return {
        "merged": merged_count,
        "staled": staled_count,
        "deleted": deleted_count,
        "kept": len(kept),
    }


def _merge_duplicates(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Merge groups sharing an exact package and label."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault((entry["package"], entry["label"]), []).append(entry)

    merged: list[dict[str, Any]] = []
    merged_count = 0
    for group in groups.values():
        if len(group) == 1:
            merged.append(dict(group[0]))
            continue
        winner = max(
            group,
            key=lambda entry: (
                entry["confidence"],
                entry["success_count"],
                _parse_iso(entry["last_seen"]),
                entry["term"],
            ),
        )
        combined = dict(winner)
        combined["confidence"] = max(entry["confidence"] for entry in group)
        combined["success_count"] = sum(entry["success_count"] for entry in group)
        combined["first_seen"] = min(
            (entry["first_seen"] for entry in group), key=_parse_iso
        )
        combined["last_seen"] = max(
            (entry["last_seen"] for entry in group), key=_parse_iso
        )
        merged.append(combined)
        merged_count += len(group) - 1
    return merged, merged_count


def _as_utc(value: datetime) -> datetime:
    """Normalize an aware or naive datetime to aware UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
