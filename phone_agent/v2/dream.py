"""Rule-based App-KB consolidation with no model or device dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from phone_agent.v2.appkb import AppKnowledgeStore, _parse_iso, _sort_key
from phone_agent.v2.experience import ExperienceWriter, load_episodes


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


_TASK_CATEGORY_TERMS = (
    (
        "travel",
        ("机票", "航班", "酒店", "火车", "打车", "旅行", "travel", "flight", "hotel"),
    ),
    (
        "commerce",
        (
            "购物",
            "商品",
            "下单",
            "订单",
            "购买",
            "淘宝",
            "京东",
            "shop",
            "order",
            "buy",
        ),
    ),
    (
        "communication",
        (
            "消息",
            "联系人",
            "电话",
            "邮件",
            "微信",
            "message",
            "contact",
            "email",
            "call",
        ),
    ),
    ("media", ("视频", "音乐", "播放", "直播", "video", "music", "play")),
    ("finance", ("支付", "银行", "转账", "账单", "payment", "bank", "transfer")),
    (
        "system",
        ("设置", "wifi", "wlan", "蓝牙", "权限", "setting", "bluetooth", "permission"),
    ),
    (
        "productivity",
        (
            "日历",
            "日程",
            "文档",
            "表格",
            "任务",
            "calendar",
            "document",
            "sheet",
            "task",
        ),
    ),
)


def _task_category(episode: dict[str, Any]) -> str:
    """Map a redacted goal to one fixed, non-identifying task category."""

    goal = str(episode.get("goal_text", "")).casefold()
    for category, terms in _TASK_CATEGORY_TERMS:
        if any(term in goal for term in terms):
            return category
    return "other"


def _merge_aggregate(
    current: dict[str, Any] | None, category: str, episodes: list[dict[str, Any]]
) -> dict[str, Any]:
    previous_count = int((current or {}).get("episodes", 0))
    previous_successes = int((current or {}).get("successes", 0))
    count = previous_count + len(episodes)
    successes = previous_successes + sum(bool(item.get("success")) for item in episodes)
    starts = [float(item.get("ts_start", 0.0)) for item in episodes]
    ends = [float(item.get("ts_end", 0.0)) for item in episodes]
    previous_start = float((current or {}).get("ts_start", 0.0))
    previous_end = float((current or {}).get("ts_end", 0.0))
    nonzero_starts = [value for value in [previous_start, *starts] if value > 0]
    return {
        "type": "episode_aggregate",
        "schema_v": 1,
        "task_category": category,
        "episodes": count,
        "successes": successes,
        "success_rate": (successes / count) if count else 0.0,
        "ts_start": min(nonzero_starts) if nonzero_starts else 0.0,
        "ts_end": max([previous_end, *ends], default=0.0),
    }


def maintain_experience(
    experience_dir: str,
    *,
    keep: int = 500,
    archive_days: int = 90,
    now: datetime | None = None,
) -> dict[str, int]:
    """Archive old full episodes into category success-rate aggregates.

    At most the newest ``keep`` full records survive. Records older than
    ``archive_days`` are also archived even when the library has not reached the
    count ceiling. The append-only log is never rewritten: one
    ``episode_archived`` tombstone makes the materialized view reproducible.
    """

    if keep < 0:
        raise ValueError("keep must be non-negative")
    if archive_days < 0:
        raise ValueError("archive_days must be non-negative")
    current_time = _as_utc(now or datetime.now(timezone.utc))
    cutoff = current_time.timestamp() - archive_days * 24 * 60 * 60
    view = load_episodes(experience_dir)
    full = sorted(
        (record for record in view.values() if record.get("type") == "episode_outcome"),
        key=lambda record: (
            float(record.get("ts_end", 0.0)),
            str(record.get("run_id", "")),
        ),
        reverse=True,
    )
    count_overflow = full[keep:] if keep else full
    aged = [record for record in full if float(record.get("ts_end", 0.0)) < cutoff]
    archive_by_id = {
        str(record["run_id"]): record
        for record in [*count_overflow, *aged]
        if record.get("run_id")
    }
    archived = list(archive_by_id.values())

    if archived:
        prior_aggregates = {
            str(record.get("task_category")): record
            for record in view.values()
            if record.get("type") == "episode_aggregate" and record.get("task_category")
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for episode in archived:
            grouped.setdefault(_task_category(episode), []).append(episode)
        aggregates = dict(prior_aggregates)
        for category, episodes in grouped.items():
            aggregates[category] = _merge_aggregate(
                prior_aggregates.get(category), category, episodes
            )
        ExperienceWriter(experience_dir).archive(
            [str(record["run_id"]) for record in archived], aggregates
        )

    return {
        "library_size": len(full),
        "archived": len(archived),
        "kept": len(full) - len(archived),
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
