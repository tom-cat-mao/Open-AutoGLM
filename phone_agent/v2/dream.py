"""Rule-based App-KB consolidation with no model or device dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from phone_agent.v2.appkb import (
    AppKnowledgeStore,
    _normalize_term,
    _parse_iso,
    _sort_key,
)
from phone_agent.v2.experience import ExperienceWriter, load_episodes


@dataclass(frozen=True)
class AliasOverwriteSignature:
    """One evidence-complete wrong-app correction found in the event log."""

    run_id: str
    term: str
    old_package: str
    new_package: str
    first_step: int
    exit_step: int
    corrected_step: int
    note_marker: str
    fingerprint: str


def _read_app_events(path: Path) -> list[dict[str, Any]]:
    """Read dictionary events from a possibly damaged append-only log."""

    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _signature_fingerprint(
    run_id: str,
    term: str,
    old_package: str,
    new_package: str,
    first_step: int,
    exit_step: int,
    corrected_step: int,
) -> str:
    payload = json.dumps(
        [
            run_id,
            _normalize_term(term),
            old_package,
            new_package,
            first_step,
            exit_step,
            corrected_step,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_alias_overwrite_signatures(
    events_path: str | Path,
    *,
    note_terms: tuple[str, ...],
) -> list[AliasOverwriteSignature]:
    """Find launch-A -> explicit wrong-app exit -> launch-B signatures.

    Evidence never crosses a run boundary.  The exit must occur one or two
    model steps after a successful launch and must carry one configured note
    marker.  A corrective launch may reuse the original name or use another
    explicit name/package, but it must resolve successfully to a different
    package.
    """

    configured = {
        str(term).strip().casefold() for term in note_terms if str(term).strip()
    }
    events = _read_app_events(Path(events_path))
    processed = {
        str(event.get("signature_fingerprint", "")).strip()
        for event in events
        if event.get("op") == "alias_overwritten"
    }
    by_run: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for order, event in enumerate(events):
        if event.get("op") != "tool_observed":
            continue
        run_id = str(event.get("run_id", "")).strip()
        if run_id:
            by_run.setdefault(run_id, []).append((order, event))

    found: list[AliasOverwriteSignature] = []
    for run_id, run_events in by_run.items():
        ordered = sorted(
            run_events,
            key=lambda item: (int(item[1].get("step", 0)), item[0]),
        )
        for index, (_order, first) in enumerate(ordered):
            if first.get("tool") != "launch_app" or first.get("success") is not True:
                continue
            term = str(first.get("term", "")).strip()
            old_package = str(first.get("package", "")).strip()
            first_step = int(first.get("step", 0))
            if not term or not old_package:
                continue

            exit_index = -1
            exit_event: dict[str, Any] | None = None
            for candidate_index in range(index + 1, len(ordered)):
                candidate = ordered[candidate_index][1]
                step_gap = int(candidate.get("step", 0)) - first_step
                if step_gap > 2:
                    break
                marker = str(candidate.get("note_marker", "")).strip()
                is_exit = candidate.get("success") is True and (
                    candidate.get("tool") == "back"
                    or (
                        candidate.get("tool") == "launch_app"
                        and str(candidate.get("package", "")).strip() != old_package
                    )
                )
                if step_gap >= 1 and is_exit and marker.casefold() in configured:
                    exit_index = candidate_index
                    exit_event = candidate
                    break
            if exit_event is None:
                continue

            corrected: dict[str, Any] | None = None
            if (
                exit_event.get("tool") == "launch_app"
                and exit_event.get("success") is True
            ):
                corrected = exit_event
            else:
                for _candidate_order, candidate in ordered[exit_index + 1 :]:
                    if (
                        candidate.get("tool") == "launch_app"
                        and candidate.get("success") is True
                        and str(candidate.get("package", "")).strip() != old_package
                    ):
                        corrected = candidate
                        break
            if corrected is None:
                continue

            new_package = str(corrected.get("package", "")).strip()
            exit_step = int(exit_event.get("step", 0))
            corrected_step = int(corrected.get("step", 0))
            fingerprint = _signature_fingerprint(
                run_id,
                term,
                old_package,
                new_package,
                first_step,
                exit_step,
                corrected_step,
            )
            if fingerprint in processed:
                continue
            found.append(
                AliasOverwriteSignature(
                    run_id=run_id,
                    term=term,
                    old_package=old_package,
                    new_package=new_package,
                    first_step=first_step,
                    exit_step=exit_step,
                    corrected_step=corrected_step,
                    note_marker=str(exit_event["note_marker"]),
                    fingerprint=fingerprint,
                )
            )
            processed.add(fingerprint)
    return found


def apply_alias_overwrites(
    store: AppKnowledgeStore, *, note_terms: tuple[str, ...]
) -> dict[str, int]:
    """Apply every unprocessed correction signature to learned aliases."""

    signatures = detect_alias_overwrite_signatures(
        store.events_path, note_terms=note_terms
    )
    overwritten = 0
    for signature in signatures:
        labels = [
            str(entry.get("label", "")).strip()
            for entry in store.entries(include_stale=False)
            if entry.get("package") == signature.new_package
            and str(entry.get("label", "")).strip()
        ]
        changed = store.overwrite_learned_alias(
            signature.term,
            signature.old_package,
            signature.new_package,
            label=labels[0] if labels else signature.new_package,
            evidence_run_id=signature.run_id,
            signature_fingerprint=signature.fingerprint,
        )
        overwritten += int(changed is not None)
    return {"candidates": len(signatures), "overwritten": overwritten}


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


def run_maintenance(
    config: Any,
    *,
    light: bool,
    store: AppKnowledgeStore | None = None,
    inventory_provider=None,
) -> dict[str, Any]:
    """Run the shared manual/auto dream lifecycle without masking outcomes."""

    summary: dict[str, Any] = {}
    active_store: AppKnowledgeStore | None = None
    try:
        active_store = store or AppKnowledgeStore(
            getattr(config, "memory_dir", "memory")
        )
        if getattr(config, "alias_overwrite_enabled", False):
            summary["alias_overwrite"] = apply_alias_overwrites(
                active_store,
                note_terms=tuple(
                    getattr(
                        config,
                        "alias_overwrite_notes",
                        ("开错", "不对", "不是", "错了", "wrong app"),
                    )
                ),
            )
        inventory = inventory_provider() if callable(inventory_provider) else None
        summary.update(consolidate(active_store, inventory=inventory, light=light))
    except Exception as exc:  # noqa: BLE001 - maintenance is deliberately fail-open
        summary = {"status": "skipped", "reason": type(exc).__name__}

    if getattr(config, "experience_enabled", False):
        try:
            summary["experience"] = maintain_experience(
                getattr(config, "experience_dir", "memory/experience"),
                keep=getattr(config, "episode_keep", 500),
                archive_days=getattr(config, "episode_archive_days", 90),
            )
        except Exception as exc:  # noqa: BLE001 - experience maintenance is optional
            summary["experience"] = {
                "status": "skipped",
                "reason": type(exc).__name__,
            }
    if getattr(config, "vec_db", None):
        try:
            from phone_agent.v2.recall import reconcile_index

            summary["vec"] = reconcile_index(
                config,
                app_store=active_store,
            )
        except Exception as exc:  # noqa: BLE001 - derived index is fail-open
            summary["vec"] = {
                "status": "skipped",
                "reason": type(exc).__name__,
            }
    return summary


def _merge_duplicates(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Merge groups sharing an exact package and label."""

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        provenance = (
            "mutable_alias"
            if entry["kind"] in {"learned", "user"}
            else str(entry["kind"])
        )
        groups.setdefault((entry["package"], entry["label"], provenance), []).append(
            entry
        )

    merged: list[dict[str, Any]] = []
    merged_count = 0
    for group in groups.values():
        if len(group) == 1:
            merged.append(dict(group[0]))
            continue
        winner = max(
            group,
            key=lambda entry: (
                int(entry["kind"] == "user"),
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
        last_successes = [
            str(entry["last_success"])
            for entry in group
            if entry.get("last_success")
        ]
        if last_successes:
            combined["last_success"] = max(last_successes, key=_parse_iso)
        merged.append(combined)
        merged_count += len(group) - 1
    return merged, merged_count


def _as_utc(value: datetime) -> datetime:
    """Normalize an aware or naive datetime to aware UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
