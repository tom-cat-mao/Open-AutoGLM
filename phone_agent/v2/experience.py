"""Privacy-minimal, append-only experience records for thin-loop runs.

The JSONL log is the source of truth.  ``episodes.json`` is only a materialized
view keyed by ``run_id`` and can always be rebuilt by replaying that log.  This
module intentionally uses only the Python standard library and never feeds
experience back into the actor; it is an observe-only data plane.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from phone_agent.config.redact import redact_context_text

EPISODE_OUTCOME_FIELDS = (
    "type",
    "schema_v",
    "run_id",
    "ts_start",
    "ts_end",
    "time_of_day",
    "day_of_week",
    "device_scope",
    "goal_text",
    "apps",
    "success",
    "reason",
    "steps",
    "tokens_total",
    "tokens_by_role",
    "warnings",
    "takeover",
    "verifier",
    "capabilities",
    "injected_lessons",
)
EXPERIENCE_EVENT_FIELDS = (
    "type",
    "schema_v",
    "run_id",
    "step",
    "ts",
    "tool",
    "result_class",
    "app_package",
    "device_scope",
)

_TIME_BUCKETS = ("night", "morning", "afternoon", "evening")
_RESULT_CLASSES = frozenset({"ok", "error", "warned"})
_VERIFIER_RESULTS = frozenset({"pass", "fail", "skipped"})
_TOKEN_ROLES = frozenset({"actor", "compact", "verifier", "reviewer", "distill"})
_TOOLS = frozenset(
    {
        "tap",
        "long_press",
        "type_text",
        "scroll",
        "swipe",
        "back",
        "home",
        "wait",
        "launch_app",
        "read_screen",
        "locate",
        "finish",
        "ask_user",
        "take_over",
        "update_task_doc",
    }
)
_PACKAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPABILITY_STATES = frozenset({"active", "off", "shadow", "pending"})
_LESSON_ID_PATTERN = re.compile(r"^les_[0-9a-f]{12,64}$")


def _clean_text(value: Any) -> str:
    """Redact every persisted string, including otherwise allowed fields."""

    return redact_context_text(str(value or ""))


def _classify_and_clean(record: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the experience privacy allowlist and return a canonical record.

    Allowlist rationale, field by field:

    * ``type``/``schema_v`` identify the public schema, not user content.
    * ``run_id`` and timestamps support joins, retention, and time analysis.
    * ``time_of_day``/``day_of_week`` are coarse scheduling context only.
    * ``device_scope`` is the explicitly allowed device namespace.
    * ``goal_text`` is the sole free-text field and is regex-redacted here.
    * ``apps``/``app_package`` contain package ids, never screen or mark text.
    * success/reason/steps/tokens/warnings/takeover/verifier are bounded outcome
      and accounting signals; any string among them is still regex-redacted.
    * ``capabilities`` contains only stable ids and one of four bounded states;
      titles, hook data, configuration values, and dependency details are absent.
    * ``injected_lessons`` contains only validated lesson ids, never lesson text.
    * ``tool`` and ``result_class`` describe execution shape without arguments or
      receipts.  In particular, ``type_text`` text, mark text, screenshots, tool
      result text, and model reasoning have no schema field and are discarded.
    """

    kind = record.get("type")
    if kind == "episode_outcome":
        ts_start = float(record.get("ts_start", 0.0))
        ts_end = float(record.get("ts_end", 0.0))
        time_of_day = _clean_text(record.get("time_of_day", "night")).lower()
        if time_of_day not in _TIME_BUCKETS:
            time_of_day = "night"
        verifier = _clean_text(record.get("verifier", "skipped")).lower()
        if verifier not in _VERIFIER_RESULTS:
            verifier = "skipped"

        apps: list[str] = []
        seen_apps: set[str] = set()
        raw_apps = record.get("apps", [])
        if isinstance(raw_apps, (list, tuple)):
            for value in raw_apps:
                package = _clean_text(value).strip()
                if (
                    package
                    and _PACKAGE_PATTERN.fullmatch(package)
                    and package not in seen_apps
                ):
                    seen_apps.add(package)
                    apps.append(package)

        raw_roles = record.get("tokens_by_role", {})
        tokens_by_role = {}
        for role, tokens in raw_roles.items() if isinstance(raw_roles, Mapping) else ():
            clean_role = _clean_text(role)
            if clean_role in _TOKEN_ROLES:
                tokens_by_role[clean_role] = max(0, int(tokens))
        takeover = record.get("takeover")
        clean_takeover = None if takeover is None else _clean_text(takeover)
        capabilities: dict[str, str] = {}
        raw_capabilities = record.get("capabilities", {})
        if isinstance(raw_capabilities, Mapping):
            for raw_cap_id, raw_state in raw_capabilities.items():
                cap_id = _clean_text(raw_cap_id).strip()
                state = _clean_text(raw_state).strip().lower()
                if (
                    _CAPABILITY_ID_PATTERN.fullmatch(cap_id)
                    and state in _CAPABILITY_STATES
                ):
                    capabilities[cap_id] = state
        injected_lessons: list[str] = []
        raw_injected_lessons = record.get("injected_lessons", [])
        if isinstance(raw_injected_lessons, (list, tuple)):
            for raw_lesson_id in raw_injected_lessons:
                lesson_id = _clean_text(raw_lesson_id).strip()
                if (
                    _LESSON_ID_PATTERN.fullmatch(lesson_id)
                    and lesson_id not in injected_lessons
                ):
                    injected_lessons.append(lesson_id)

        # Construct in the exact shared WP-I1/WP-I2 field order.
        return {
            "type": "episode_outcome",
            "schema_v": 1,
            "run_id": _clean_text(record.get("run_id")),
            "ts_start": ts_start,
            "ts_end": ts_end,
            "time_of_day": time_of_day,
            "day_of_week": int(record.get("day_of_week", 0)),
            "device_scope": _clean_text(record.get("device_scope")),
            "goal_text": _clean_text(record.get("goal_text")),
            "apps": apps,
            "success": bool(record.get("success", False)),
            "reason": _clean_text(record.get("reason")),
            "steps": max(0, int(record.get("steps", 0))),
            "tokens_total": max(0, int(record.get("tokens_total", 0))),
            "tokens_by_role": tokens_by_role,
            "warnings": max(0, int(record.get("warnings", 0))),
            "takeover": clean_takeover,
            "verifier": verifier,
            "capabilities": capabilities,
            "injected_lessons": injected_lessons,
        }

    if kind == "experience_event":
        result_class = _clean_text(record.get("result_class", "error")).lower()
        if result_class not in _RESULT_CLASSES:
            result_class = "error"
        app_package = record.get("app_package")
        clean_package = None if app_package is None else _clean_text(app_package)
        if clean_package is not None and not _PACKAGE_PATTERN.fullmatch(clean_package):
            clean_package = None
        tool = _clean_text(record.get("tool"))
        if tool not in _TOOLS:
            tool = "unknown"

        # Tool args/results are deliberately absent: only this fixed shape lands.
        return {
            "type": "experience_event",
            "schema_v": 1,
            "run_id": _clean_text(record.get("run_id")),
            "step": max(0, int(record.get("step", 0))),
            "ts": float(record.get("ts", 0.0)),
            "tool": tool,
            "result_class": result_class,
            "app_package": clean_package,
            "device_scope": _clean_text(record.get("device_scope")),
        }

    raise ValueError(f"unsupported experience record type: {kind!r}")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _replay_events(events_path: Path) -> dict[str, dict[str, Any]]:
    episodes: dict[str, dict[str, Any]] = {}
    if not events_path.exists():
        return episodes
    with events_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "episode_outcome":
                try:
                    clean = _classify_and_clean(event)
                except (TypeError, ValueError):
                    continue
                if clean["run_id"]:
                    episodes[clean["run_id"]] = clean
            elif event.get("type") == "episode_archived":
                for run_id in event.get("run_ids", []):
                    episodes.pop(str(run_id), None)
                aggregates = event.get("aggregates", {})
                if isinstance(aggregates, Mapping):
                    for key in [
                        key for key in episodes if key.startswith("aggregate:")
                    ]:
                        episodes.pop(key, None)
                    for category, aggregate in aggregates.items():
                        if isinstance(aggregate, Mapping):
                            episodes[f"aggregate:{category}"] = dict(aggregate)
    return episodes


def load_episodes(
    experience_dir: str | os.PathLike[str] = "memory/experience",
) -> dict[str, dict[str, Any]]:
    """Load the run-id-indexed view, rebuilding it from JSONL if damaged."""

    root = Path(experience_dir)
    materialized_path = root / "episodes.json"
    try:
        payload = json.loads(materialized_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in payload.items()
        ):
            raise ValueError("episodes.json must be an object of records")
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        rebuilt = _replay_events(root / "events.jsonl")
        _atomic_write_json(materialized_path, rebuilt)
        return rebuilt


class ExperienceWriter:
    """Append allowlisted events and maintain the rebuildable episode view."""

    def __init__(
        self, experience_dir: str | os.PathLike[str] = "memory/experience"
    ) -> None:
        self.root = Path(experience_dir)
        self.events_path = self.root / "events.jsonl"
        self.episodes_path = self.root / "episodes.json"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        # Replay on open so a crash after the durable append but before the
        # materialized rewrite cannot leave a valid-looking yet stale view.
        _atomic_write_json(self.episodes_path, _replay_events(self.events_path))

    def _append_raw(self, event: Mapping[str, Any]) -> None:
        needs_newline = False
        if self.events_path.stat().st_size:
            with self.events_path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                needs_newline = stream.read(1) != b"\n"
        with self.events_path.open("a", encoding="utf-8") as stream:
            if needs_newline:
                stream.write("\n")
            stream.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def append_outcome(
        self, outcome: Mapping[str, Any] | None = None, **fields: Any
    ) -> dict[str, Any]:
        """Append one canonical EpisodeOutcome and refresh its view entry."""

        raw = dict(outcome or {})
        raw.update(fields)
        raw["type"] = "episode_outcome"
        clean = _classify_and_clean(raw)
        if not clean["run_id"]:
            raise ValueError("episode_outcome.run_id must not be empty")
        with self._lock:
            self._append_raw(clean)
            episodes = _replay_events(self.events_path)
            _atomic_write_json(self.episodes_path, episodes)
        return clean

    def append_event(
        self, event: Mapping[str, Any] | None = None, **fields: Any
    ) -> dict[str, Any]:
        """Append one canonical ExperienceEvent; never persist tool payloads."""

        raw = dict(event or {})
        raw.update(fields)
        raw["type"] = "experience_event"
        clean = _classify_and_clean(raw)
        if not clean["run_id"]:
            raise ValueError("experience_event.run_id must not be empty")
        with self._lock:
            self._append_raw(clean)
        return clean

    def archive(
        self, run_ids: list[str], aggregates: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Append one archive tombstone and materialize the resulting view."""

        clean_ids = list(dict.fromkeys(_clean_text(item) for item in run_ids if item))
        clean_aggregates: dict[str, dict[str, Any]] = {}
        for raw_category, raw in aggregates.items():
            category = _clean_text(raw_category).strip()
            if not category or not isinstance(raw, Mapping):
                continue
            count = max(0, int(raw.get("episodes", 0)))
            successes = min(count, max(0, int(raw.get("successes", 0))))
            clean_aggregates[category] = {
                "type": "episode_aggregate",
                "schema_v": 1,
                "task_category": category,
                "episodes": count,
                "successes": successes,
                "success_rate": (successes / count) if count else 0.0,
                "ts_start": float(raw.get("ts_start", 0.0)),
                "ts_end": float(raw.get("ts_end", 0.0)),
            }
        event = {
            "type": "episode_archived",
            "schema_v": 1,
            "ts": time.time(),
            "run_ids": clean_ids,
            "aggregates": clean_aggregates,
        }
        with self._lock:
            self._append_raw(event)
            episodes = _replay_events(self.events_path)
            _atomic_write_json(self.episodes_path, episodes)


def classify_tool_result(result: Any, error: BaseException | None = None) -> str:
    """Reduce an in-memory tool receipt to ``ok|error|warned`` only."""

    if error is not None:
        return "error"
    content = getattr(result, "content", result)
    if isinstance(content, list):
        text = next(
            (
                str(block.get("text", ""))
                for block in content
                if isinstance(block, Mapping) and block.get("type") == "text"
            ),
            "",
        )
    else:
        text = str(content or "")
    stripped = text.lstrip()
    if "已拦截（未执行）" in stripped or stripped.startswith("⚠️ 已拦截"):
        return "warned"
    if getattr(result, "status", None) == "error":
        return "error"
    lowered = stripped.casefold()
    error_prefixes = (
        "error",
        "ambiguous",
        "denied",
        "unknown app",
        "not installed",
        "未定位",
        "定位失败",
        "未写入",
        "路线仍有未完成项",
        "验收未通过",
        "验收器再次驳回",
    )
    return "error" if lowered.startswith(error_prefixes) else "ok"


__all__ = [
    "EPISODE_OUTCOME_FIELDS",
    "EXPERIENCE_EVENT_FIELDS",
    "ExperienceWriter",
    "_classify_and_clean",
    "classify_tool_result",
    "load_episodes",
]
