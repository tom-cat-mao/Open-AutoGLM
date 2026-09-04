"""Offline lesson distillation and human-governed promotion.

The mutation path is deliberately outside the actor hot path: it reads
privacy-minimal episode outcomes and emits proposal/review events.  The sole
runtime bridge is a bounded, read-only selector for approved lessons; actor
message construction remains owned by :mod:`phone_agent.v2.agent`.
events.jsonl is authoritative; lessons.json is a rebuildable current-version
view.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from phone_agent.v2.middleware._tokens import (
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
)
from phone_agent.v2.usage import UsageLedger

LESSON_EVENT_TYPES = frozenset(
    {
        "lesson_proposed",
        "lesson_approved",
        "lesson_revoked",
        "lesson_demoted",
        "lesson_superseded",
    }
)
LESSON_STATUSES = frozenset({"proposed", "approved", "revoked"})
LESSON_FIELDS = (
    "lesson_id",
    "schema_v",
    "version",
    "status",
    "text",
    "scope",
    "evidence",
    "support_count",
    "task_keys",
    "conflicts",
    "created_ts",
    "source",
)
_SCOPE_FIELDS = frozenset({"device", "app", "app_version"})
_EVIDENCE_FIELDS = frozenset({"run_id", "note"})
_LESSON_ID = re.compile(r"^les_[0-9a-f]{12,64}$")
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")

_TASK_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("search_flight", ("机票", "航班", "flight")),
    ("search_hotel", ("酒店", "hotel")),
    ("open_app", ("打开", "启动", "open", "launch")),
    ("search", ("查询", "搜索", "查找", "search", "find")),
    ("send_message", ("发送", "消息", "send", "message")),
    ("change_setting", ("设置", "wifi", "wlan", "蓝牙", "setting")),
)
_NEGATIONS = (
    "不要",
    "禁止",
    "避免",
    "不得",
    "不能",
    "不应该",
    "不应",
    "切勿",
    "never",
    "do not",
    "don't",
    "should not",
    "must not",
)
_MODALS = ("应该", "应当", "必须", "应", "always", "should", "must")


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _nullable_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scope.{field} must be a non-empty string or null")
    return value.strip()


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or item != _single_line(item):
            raise ValueError(f"{field} values must be non-empty normalized strings")
        if item in result:
            raise ValueError(f"{field} values must be unique")
        result.append(item)
    return result


@dataclass(frozen=True)
class LessonCandidate:
    """Strict, versioned lesson proposal schema."""

    lesson_id: str
    schema_v: int
    version: int
    status: str
    text: str
    scope: dict[str, str | None]
    evidence: list[dict[str, str]]
    support_count: int
    task_keys: list[str]
    conflicts: list[str]
    created_ts: float
    source: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LessonCandidate":
        """Validate an exact schema object without accepting extra fields."""

        if not isinstance(payload, Mapping):
            raise TypeError("lesson candidate must be an object")
        if set(payload) != set(LESSON_FIELDS):
            missing = sorted(set(LESSON_FIELDS) - set(payload))
            extra = sorted(set(payload) - set(LESSON_FIELDS))
            raise ValueError(f"lesson fields mismatch: missing={missing}, extra={extra}")

        lesson_id = payload["lesson_id"]
        if not isinstance(lesson_id, str) or not _LESSON_ID.fullmatch(lesson_id):
            raise ValueError("lesson_id must be les_<12-64 lowercase hex chars>")
        schema_v = payload["schema_v"]
        if not isinstance(schema_v, int) or isinstance(schema_v, bool) or schema_v != 1:
            raise ValueError("schema_v must be 1")
        version = payload["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        status = payload["status"]
        if not isinstance(status, str) or status not in LESSON_STATUSES:
            raise ValueError(f"status must be one of {sorted(LESSON_STATUSES)!r}")
        text = payload["text"]
        if not isinstance(text, str) or not text.strip() or text != _single_line(text):
            raise ValueError("text must be one non-empty normalized line")

        raw_scope = payload["scope"]
        if not isinstance(raw_scope, Mapping) or set(raw_scope) != _SCOPE_FIELDS:
            raise ValueError("scope must contain exactly device/app/app_version")
        scope = {
            "device": _nullable_text(raw_scope["device"], "device"),
            "app": _nullable_text(raw_scope["app"], "app"),
            "app_version": _nullable_text(
                raw_scope["app_version"], "app_version"
            ),
        }
        if scope["app"] is not None and not _PACKAGE.fullmatch(scope["app"]):
            raise ValueError("scope.app must be a package name or null")

        raw_evidence = payload["evidence"]
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence must be an array")
        evidence: list[dict[str, str]] = []
        seen_runs: set[str] = set()
        for item in raw_evidence:
            if not isinstance(item, Mapping) or set(item) != _EVIDENCE_FIELDS:
                raise ValueError("each evidence item must contain run_id and note")
            run_id = item["run_id"]
            note = item["note"]
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("evidence.run_id must be a non-empty string")
            if run_id in seen_runs:
                raise ValueError("evidence.run_id values must be unique")
            if (
                not isinstance(note, str)
                or not note.strip()
                or note != _single_line(note)
            ):
                raise ValueError("evidence.note must be a non-empty normalized line")
            seen_runs.add(run_id)
            evidence.append({"run_id": run_id, "note": note})

        support_count = payload["support_count"]
        if (
            not isinstance(support_count, int)
            or isinstance(support_count, bool)
            or support_count < 0
        ):
            raise ValueError("support_count must be a non-negative integer")
        if support_count != len(evidence):
            raise ValueError("support_count must equal the unique evidence count")

        task_keys = _string_list(payload["task_keys"], "task_keys")
        conflicts = _string_list(payload["conflicts"], "conflicts")
        if isinstance(payload["created_ts"], bool):
            raise ValueError("created_ts must be a finite non-negative number")
        try:
            created_ts = float(payload["created_ts"])
        except (TypeError, ValueError) as exc:
            raise ValueError("created_ts must be a finite non-negative number") from exc
        if not math.isfinite(created_ts) or created_ts < 0:
            raise ValueError("created_ts must be a finite non-negative number")
        if payload["source"] != "distill":
            raise ValueError("source must be 'distill'")

        return cls(
            lesson_id=lesson_id,
            schema_v=1,
            version=version,
            status=status,
            text=text,
            scope=scope,
            evidence=evidence,
            support_count=support_count,
            task_keys=task_keys,
            conflicts=conflicts,
            created_ts=created_ts,
            source="distill",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible object in schema field order."""

        return {
            "lesson_id": self.lesson_id,
            "schema_v": self.schema_v,
            "version": self.version,
            "status": self.status,
            "text": self.text,
            "scope": dict(self.scope),
            "evidence": [dict(item) for item in self.evidence],
            "support_count": self.support_count,
            "task_keys": list(self.task_keys),
            "conflicts": list(self.conflicts),
            "created_ts": self.created_ts,
            "source": self.source,
        }


def make_lesson_id(text: str, scope: Mapping[str, Any]) -> str:
    """Return a stable semantic id for a lesson text and scope."""

    payload = json.dumps(
        {"text": _single_line(text).casefold(), "scope": dict(scope)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "les_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _replay_lesson_events(events_path: Path) -> dict[str, LessonCandidate]:
    current: dict[str, LessonCandidate] = {}
    if not events_path.exists():
        return current
    with events_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                kind = event["type"]
                if kind not in LESSON_EVENT_TYPES or event.get("schema_v") != 1:
                    continue
                if kind in {"lesson_proposed", "lesson_superseded"}:
                    candidate = LessonCandidate.from_dict(event["lesson"])
                    if candidate.status != "proposed":
                        continue
                    if kind == "lesson_proposed":
                        if candidate.lesson_id in current or candidate.version != 1:
                            continue
                    else:
                        prior = current.get(candidate.lesson_id)
                        if (
                            prior is None
                            or event.get("lesson_id") != candidate.lesson_id
                            or event.get("from_version") != prior.version
                            or candidate.version != prior.version + 1
                        ):
                            continue
                    current[candidate.lesson_id] = candidate
                    continue
                lesson_id = str(event["lesson_id"])
                candidate = current.get(lesson_id)
                if candidate is None or event.get("version") != candidate.version:
                    continue
                if kind == "lesson_approved" and candidate.status == "proposed":
                    current[lesson_id] = replace(candidate, status="approved")
                elif kind == "lesson_revoked" and candidate.status != "revoked":
                    current[lesson_id] = replace(candidate, status="revoked")
                elif kind == "lesson_demoted" and candidate.status == "approved":
                    # Back to proposed at the same version; there is no
                    # reinstatement path, so a revoked lesson stays revoked.
                    current[lesson_id] = replace(candidate, status="proposed")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    return current


class LessonStore:
    """Append lesson lifecycle events and maintain their current-version view."""

    def __init__(self, lessons_dir: str | os.PathLike[str] = "memory/lessons") -> None:
        self.root = Path(lessons_dir)
        self.events_path = self.root / "events.jsonl"
        self.lessons_path = self.root / "lessons.json"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        self._lessons = _replay_lesson_events(self.events_path)
        self._write_view()

    def lessons(self, *, status: str | None = None) -> list[LessonCandidate]:
        with self._lock:
            values = [
                candidate
                for candidate in self._lessons.values()
                if status is None or candidate.status == status
            ]
        return sorted(values, key=lambda item: (item.created_ts, item.lesson_id))

    def get(self, lesson_id: str) -> LessonCandidate | None:
        with self._lock:
            return self._lessons.get(lesson_id)

    def propose(self, candidate: LessonCandidate) -> LessonCandidate:
        candidate = LessonCandidate.from_dict(candidate.to_dict())
        if candidate.status != "proposed":
            raise ValueError("new lesson candidates must be proposed")
        with self._lock:
            prior = self._lessons.get(candidate.lesson_id)
            if prior is None:
                if candidate.version != 1:
                    raise ValueError("a new lesson must start at version 1")
                self._append(
                    {
                        "type": "lesson_proposed",
                        "schema_v": 1,
                        "ts": time.time(),
                        "lesson": candidate.to_dict(),
                    }
                )
                saved = candidate
            elif _proposal_payload(prior) == _proposal_payload(candidate):
                return prior
            else:
                # A changed evidence/conflict payload is a new proposal version,
                # even when v1 was approved; it still requires another human
                # approval and can never inherit approved status automatically.
                saved = replace(candidate, version=prior.version + 1, status="proposed")
                self._append(
                    {
                        "type": "lesson_superseded",
                        "schema_v": 1,
                        "ts": time.time(),
                        "lesson_id": prior.lesson_id,
                        "from_version": prior.version,
                        "lesson": saved.to_dict(),
                    }
                )
            self._lessons[saved.lesson_id] = saved
            self._write_view()
            return saved

    def approve(self, lesson_id: str) -> LessonCandidate:
        with self._lock:
            candidate = self._require(lesson_id)
            if candidate.status != "proposed":
                raise ValueError("only a proposed lesson can be approved")
            approved = replace(candidate, status="approved")
            self._append(
                {
                    "type": "lesson_approved",
                    "schema_v": 1,
                    "ts": time.time(),
                    "lesson_id": lesson_id,
                    "version": candidate.version,
                }
            )
            self._lessons[lesson_id] = approved
            self._write_view()
            return approved

    def revoke(self, lesson_id: str, reason: str) -> LessonCandidate:
        clean_reason = _single_line(reason)
        if not clean_reason:
            raise ValueError("revoke reason must not be empty")
        with self._lock:
            candidate = self._require(lesson_id)
            if candidate.status == "revoked":
                raise ValueError("lesson is already revoked")
            revoked = replace(candidate, status="revoked")
            self._append(
                {
                    "type": "lesson_revoked",
                    "schema_v": 1,
                    "ts": time.time(),
                    "lesson_id": lesson_id,
                    "version": candidate.version,
                    "reason": clean_reason,
                }
            )
            self._lessons[lesson_id] = revoked
            self._write_view()
            return revoked

    def demote(self, lesson_id: str, reason: str) -> LessonCandidate:
        """Withdraw an approved lesson back to proposed, keeping its version.

        This is the evidence-loss counterpart of :meth:`approve`: a lesson whose
        cited episodes no longer satisfy Rule-of-3 stops being injectable and
        needs another human approval.  Revoked lessons are never reinstated.
        """

        clean_reason = _single_line(reason)
        if not clean_reason:
            raise ValueError("demote reason must not be empty")
        with self._lock:
            candidate = self._require(lesson_id)
            if candidate.status != "approved":
                raise ValueError("only an approved lesson can be demoted")
            demoted = replace(candidate, status="proposed")
            self._append(
                {
                    "type": "lesson_demoted",
                    "schema_v": 1,
                    "ts": time.time(),
                    "lesson_id": lesson_id,
                    "version": candidate.version,
                    "reason": clean_reason,
                }
            )
            self._lessons[lesson_id] = demoted
            self._write_view()
            return demoted

    def supersede(self, lesson_id: str, text: str) -> LessonCandidate:
        """Create a proposed revision while retaining the stable lesson id."""

        clean_text = _single_line(text)
        if not clean_text:
            raise ValueError("replacement text must not be empty")
        with self._lock:
            prior = self._require(lesson_id)
            revision = replace(
                prior,
                version=prior.version + 1,
                status="proposed",
                text=clean_text,
                conflicts=[],
                created_ts=time.time(),
            )
            self._append(
                {
                    "type": "lesson_superseded",
                    "schema_v": 1,
                    "ts": time.time(),
                    "lesson_id": lesson_id,
                    "from_version": prior.version,
                    "lesson": revision.to_dict(),
                }
            )
            self._lessons[lesson_id] = revision
            self._write_view()
            return revision

    def _require(self, lesson_id: str) -> LessonCandidate:
        candidate = self._lessons.get(lesson_id)
        if candidate is None:
            raise KeyError(f"unknown lesson: {lesson_id}")
        return candidate

    def _append(self, event: Mapping[str, Any]) -> None:
        needs_newline = False
        if self.events_path.stat().st_size:
            with self.events_path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                needs_newline = stream.read(1) != b"\n"
        with self.events_path.open("a", encoding="utf-8") as stream:
            if needs_newline:
                stream.write("\n")
            stream.write(
                json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def _write_view(self) -> None:
        payload = [
            candidate.to_dict()
            for candidate in sorted(
                self._lessons.values(),
                key=lambda item: (item.created_ts, item.lesson_id),
            )
        ]
        _atomic_write(self.lessons_path, payload)


def _proposal_payload(candidate: LessonCandidate) -> dict[str, Any]:
    payload = candidate.to_dict()
    for key in ("version", "status", "created_ts"):
        payload.pop(key)
    return payload


def load_lessons(
    lessons_dir: str | os.PathLike[str] = "memory/lessons",
) -> list[LessonCandidate]:
    """Rebuild and return the current lesson view from authoritative events."""

    return LessonStore(lessons_dir).lessons()


def select_lessons_for_injection(
    lessons_dir: str | os.PathLike[str],
    *,
    device_scope: str | None,
    max_items: int,
    max_tokens: int,
) -> list[LessonCandidate]:
    """Read a bounded approved-only run-start snapshot from ``lessons.json``.

    This path intentionally does not construct :class:`LessonStore`: opening a
    runtime run must never create or rebuild lesson state.  A missing or damaged
    materialized view fails open to no injection.  App-scoped lessons are
    excluded because the foreground app is not yet known at run start; a future
    event-triggered injector may resolve that narrower scope.
    """

    try:
        item_limit = int(max_items)
        token_limit = int(max_tokens)
    except (TypeError, ValueError):
        return []
    if item_limit <= 0 or token_limit <= 0:
        return []

    path = Path(lessons_dir) / "lessons.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        lessons = [LessonCandidate.from_dict(item) for item in payload]
        if len({lesson.lesson_id for lesson in lessons}) != len(lessons):
            return []
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []

    local_device = _single_line(device_scope).removeprefix("device:")
    if local_device == "unknown":
        local_device = ""
    eligible = [
        lesson
        for lesson in lessons
        if lesson.status == "approved"
        and lesson.scope["device"] in {None, local_device or None}
        # App and app-version scope cannot be established at run start.
        and lesson.scope["app"] is None
        and lesson.scope["app_version"] is None
    ]
    selected = sorted(
        eligible,
        key=lambda lesson: (-lesson.version, -lesson.created_ts, lesson.lesson_id),
    )[:item_limit]

    while selected and sum(estimate_text_tokens(item.text) for item in selected) > token_limit:
        selected.pop()
    return selected


def emergency_revoke_lesson(
    lessons_dir: str | os.PathLike[str], lesson_id: str
) -> bool:
    """Persist a runner-originated revocation; unknown/corrupt state is a no-op."""

    try:
        store = LessonStore(lessons_dir)
        current = store.get(lesson_id)
        if current is None:
            return False
        if current.status == "revoked":
            return True
        store.revoke(lesson_id, "runner emergency revoke")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


@dataclass(frozen=True)
class PromotionEvaluation:
    eligible: bool
    candidate: LessonCandidate
    reasons: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.eligible


def _semantic_signature(text: str) -> tuple[int, str]:
    lowered = _single_line(text).casefold()
    polarity = -1 if any(term in lowered for term in _NEGATIONS) else 1
    for term in (*_NEGATIONS, *_MODALS):
        lowered = lowered.replace(term, "")
    base = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)
    return polarity, base


def _opposes(left: LessonCandidate, right: LessonCandidate) -> bool:
    if left.scope != right.scope:
        return False
    left_polarity, left_base = _semantic_signature(left.text)
    right_polarity, right_base = _semantic_signature(right.text)
    return bool(
        left_base
        and left_base == right_base
        and left_polarity != right_polarity
    )


def evaluate_promotion(
    candidate: LessonCandidate,
    episodes: Sequence[Mapping[str, Any]],
    *,
    approved_lessons: Sequence[LessonCandidate] = (),
) -> PromotionEvaluation:
    """Apply Rule-of-3 and conservative same-scope contradiction detection."""

    reasons = [
        item
        for item in candidate.conflicts
        if not item.startswith(("rule:", "approved_conflict:"))
    ]
    episode_ids = {
        str(item.get("run_id"))
        for item in episodes
        if item.get("type") == "episode_outcome" and item.get("run_id")
    }
    evidence_ids = {item["run_id"] for item in candidate.evidence}
    verified_support = len(evidence_ids & episode_ids)
    cited_episodes = [
        item for item in episodes if str(item.get("run_id")) in evidence_ids
    ]
    verified_task_keys = {_task_key(item) for item in cited_episodes}
    if candidate.support_count < 3:
        reasons.append(f"rule:support_count={candidate.support_count}<3")
    if verified_support < candidate.support_count:
        reasons.append(
            f"rule:verified_evidence={verified_support}<{candidate.support_count}"
        )
    supported_task_keys = set(candidate.task_keys) & verified_task_keys
    if len(supported_task_keys) < 2:
        reasons.append(f"rule:task_keys={len(supported_task_keys)}<2")
    if not set(candidate.task_keys) <= verified_task_keys:
        reasons.append("rule:task_keys_not_supported_by_evidence")
    for approved in approved_lessons:
        if approved.status != "approved" or approved.lesson_id == candidate.lesson_id:
            continue
        if _opposes(candidate, approved):
            reasons.append(f"approved_conflict:{approved.lesson_id}@v{approved.version}")
    reasons = list(dict.fromkeys(reasons))
    evaluated = replace(candidate, conflicts=reasons, status="proposed")
    return PromotionEvaluation(not reasons, evaluated, tuple(reasons))


def _task_key(episode: Mapping[str, Any]) -> str:
    """Derive a non-identifying task key without exposing goal text to the model."""

    goal = _single_line(episode.get("goal_text")).casefold()
    for key, terms in _TASK_TERMS:
        if any(term in goal for term in terms):
            return key
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:12]
    return f"task_{digest}"


def _episode_rows(events_path: Path) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not events_path.exists():
        return []
    with events_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "episode_outcome"
                and event.get("run_id")
            ):
                rows[str(event["run_id"])] = event
    return sorted(
        rows.values(),
        key=lambda item: (float(item.get("ts_end", 0.0)), str(item["run_id"])),
    )


def _episode_sort_key(item: Mapping[str, Any]) -> tuple[float, str]:
    return float(item.get("ts_end", 0.0)), str(item.get("run_id", ""))


def _eligible_groups(
    episodes: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group by app set and retain only evidence-qualified cohorts."""

    by_apps: dict[tuple[str, ...], dict[bool, list[dict[str, Any]]]] = {}
    for raw in episodes:
        apps = tuple(sorted({str(app) for app in raw.get("apps", []) if app}))
        episode = dict(raw)
        by_apps.setdefault(apps, {False: [], True: []})[
            bool(episode.get("success"))
        ].append(episode)

    eligible: list[list[dict[str, Any]]] = []
    for buckets in by_apps.values():
        failures = buckets[False]
        successes = buckets[True]
        contrasting = bool(
            failures
            and successes
            and min(float(item.get("ts_end", 0.0)) for item in failures)
            < max(float(item.get("ts_end", 0.0)) for item in successes)
        )
        if contrasting:
            eligible.append(sorted([*failures, *successes], key=_episode_sort_key))
            continue
        repeated_reasons = {
            reason
            for reason, count in Counter(
                _single_line(item.get("reason")) for item in failures
            ).items()
            if reason and count >= 2
        }
        for reason in sorted(repeated_reasons):
            eligible.append(
                sorted(
                    [
                        item
                        for item in failures
                        if _single_line(item.get("reason")) == reason
                    ],
                    key=_episode_sort_key,
                )
            )
    return eligible


def _scope_values(group: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    devices = {
        str(item.get("device_scope", "")).removeprefix("device:")
        for item in group
        if str(item.get("device_scope", "")).strip()
    }
    apps = {str(app) for item in group for app in item.get("apps", []) if app}
    return {"devices": devices, "apps": apps}


def _prompt_rows(group: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Render model evidence with no goal text or other user-authored content."""

    return [
        {
            "run_id": str(item["run_id"]),
            "apps": sorted({str(app) for app in item.get("apps", []) if app}),
            "success": bool(item.get("success")),
            "reason": _single_line(item.get("reason"))[:120],
            "device": _single_line(item.get("device_scope"))
            .removeprefix("device:")[:120]
            or None,
            "task_key": _task_key(item),
            "ts_end": float(item.get("ts_end", 0.0)),
        }
        for item in group
    ]


def _build_distill_messages(group: Sequence[Mapping[str, Any]]) -> list[Any]:
    from langchain_core.messages import HumanMessage, SystemMessage

    system = SystemMessage(
        content=(
            "你是离线经验提炼器，只能依据给定的隐私最小化 episode 摘要提出行为规则。"
            "只输出严格 JSON 数组，不要 Markdown、解释或代码围栏。每个元素必须且只能包含："
            + ", ".join(LESSON_FIELDS)
            + "。status 必须是 proposed，schema_v/version 必须是 1，source 必须是 distill；"
            "lesson_id 使用 les_ 加 12-64 位小写十六进制。text 只能是一句行为规则及适用条件，"
            "禁止复述或猜测用户输入。scope 的 device/app 只能逐字选自输入，app_version 必须为 null。"
            "evidence 只能引用输入 run_id；support_count 必须等于去重 evidence 数；"
            "task_keys 只能选输入 task_key。无法形成有证据的规则时输出 []。"
        )
    )
    human = HumanMessage(
        content=(
            "从以下 episode 摘要提炼候选：\n"
            + json.dumps(_prompt_rows(group), ensure_ascii=False, sort_keys=True)
        )
    )
    return [system, human]


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
            for block in content
        )
    return str(content)


def _strict_json_loads(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def _contains_goal_leak(
    candidate: LessonCandidate, group: Sequence[Mapping[str, Any]]
) -> bool:
    output_text = _single_line(
        " ".join(
            [
                candidate.text,
                *(item["note"] for item in candidate.evidence),
                *candidate.conflicts,
            ]
        )
    ).casefold()
    for episode in group:
        goal = _single_line(episode.get("goal_text")).casefold()
        if goal and goal in output_text:
            return True
    return False


def _has_distill_evidence(
    candidate: LessonCandidate, group: Sequence[Mapping[str, Any]]
) -> bool:
    """Require the candidate's own citations to prove a supported pattern."""

    evidence_ids = {item["run_id"] for item in candidate.evidence}
    cited = [item for item in group if str(item["run_id"]) in evidence_ids]
    failures = [item for item in cited if not bool(item.get("success"))]
    successes = [item for item in cited if bool(item.get("success"))]
    if failures and successes:
        return min(float(item.get("ts_end", 0.0)) for item in failures) < max(
            float(item.get("ts_end", 0.0)) for item in successes
        )
    failure_reasons = Counter(_single_line(item.get("reason")) for item in failures)
    return any(reason and count >= 2 for reason, count in failure_reasons.items())


def _validate_model_candidates(
    response: Any, group: Sequence[Mapping[str, Any]]
) -> list[LessonCandidate]:
    payload = _strict_json_loads(_response_text(response))
    if not isinstance(payload, list):
        raise ValueError("distill output must be a JSON array")
    allowed_runs = {str(item["run_id"]) for item in group}
    episode_by_id = {str(item["run_id"]): item for item in group}
    allowed_tasks = {_task_key(item) for item in group}
    scope_values = _scope_values(group)
    candidates: list[LessonCandidate] = []
    for raw in payload:
        candidate = LessonCandidate.from_dict(raw)
        if candidate.status != "proposed" or candidate.version != 1:
            raise ValueError("distilled lessons must be proposed version 1")
        evidence_ids = {item["run_id"] for item in candidate.evidence}
        if not evidence_ids or not evidence_ids <= allowed_runs:
            raise ValueError("candidate evidence must reference this group")
        if not _has_distill_evidence(candidate, group):
            raise ValueError("candidate citations do not prove an eligible pattern")
        cited = [item for item in group if str(item["run_id"]) in evidence_ids]
        cited_tasks = {_task_key(item) for item in cited}
        if not set(candidate.task_keys) <= allowed_tasks:
            raise ValueError("candidate task_keys must come from this group")
        if not set(candidate.task_keys) <= cited_tasks:
            raise ValueError("candidate task_keys must be supported by its evidence")
        if candidate.scope["device"] not in {None, *scope_values["devices"]}:
            raise ValueError("candidate device scope was not observed")
        if candidate.scope["app"] not in {None, *scope_values["apps"]}:
            raise ValueError("candidate app scope was not observed")
        if candidate.scope["app_version"] is not None:
            raise ValueError("episode schema contains no app_version evidence")
        if _contains_goal_leak(candidate, group):
            raise ValueError("candidate leaks episode goal_text")
        canonical_evidence = []
        for item in candidate.evidence:
            episode = episode_by_id[item["run_id"]]
            outcome = "success" if bool(episode.get("success")) else "failure"
            reason = _single_line(episode.get("reason"))[:120]
            canonical_evidence.append(
                {
                    "run_id": item["run_id"],
                    "note": f"{outcome}:{reason}" if reason else outcome,
                }
            )
        candidate = replace(
            candidate,
            lesson_id=make_lesson_id(candidate.text, candidate.scope),
            evidence=sorted(canonical_evidence, key=lambda item: item["run_id"]),
            task_keys=sorted(candidate.task_keys),
            conflicts=sorted(candidate.conflicts),
            created_ts=time.time(),
        )
        candidates.append(candidate)
    return candidates


@dataclass(frozen=True)
class DistillResult:
    groups_considered: int
    groups_rejected: int
    proposed: tuple[LessonCandidate, ...]
    tokens_total: int
    tokens_by_role: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups_considered": self.groups_considered,
            "groups_rejected": self.groups_rejected,
            "proposed": [item.to_dict() for item in self.proposed],
            "tokens_total": self.tokens_total,
            "tokens_by_role": dict(self.tokens_by_role),
        }


def distill_lessons(
    experience_events: str | os.PathLike[str],
    lessons_dir: str | os.PathLike[str],
    *,
    model: Any,
    ledger: UsageLedger | None = None,
    token_budget: int | None = None,
) -> DistillResult:
    """Distill evidence-qualified groups into proposed lessons only."""

    episodes = _episode_rows(Path(experience_events))
    groups = _eligible_groups(episodes)
    active_ledger = ledger or UsageLedger()
    store = LessonStore(lessons_dir)
    proposed: list[LessonCandidate] = []
    rejected = 0
    for group in groups:
        if token_budget is not None and active_ledger.total >= token_budget:
            rejected += 1
            continue
        messages = _build_distill_messages(group)
        request_estimate = estimate_context_tokens(messages)
        if (
            token_budget is not None
            and active_ledger.total + request_estimate > token_budget
        ):
            rejected += 1
            continue
        try:
            response = model.invoke(messages)
        except Exception:  # noqa: BLE001 - reject this offline group and continue
            rejected += 1
            active_ledger.record(
                "distill", estimate_tokens=request_estimate
            )
            continue
        estimate = request_estimate + estimate_message_tokens(response)
        active_ledger.record("distill", response, estimate_tokens=estimate)
        if token_budget is not None and active_ledger.total > token_budget:
            rejected += 1
            break
        try:
            candidates = _validate_model_candidates(response, group)
        except (TypeError, ValueError, json.JSONDecodeError):
            rejected += 1
            continue
        if not candidates:
            rejected += 1
            continue
        for candidate in candidates:
            evaluation = evaluate_promotion(
                candidate,
                group,
                approved_lessons=store.lessons(status="approved"),
            )
            prior = store.get(evaluation.candidate.lesson_id)
            saved = store.propose(evaluation.candidate)
            if prior is None or saved.version > prior.version:
                proposed.append(saved)
    return DistillResult(
        groups_considered=len(groups),
        groups_rejected=rejected,
        proposed=tuple(proposed),
        tokens_total=active_ledger.total,
        tokens_by_role=active_ledger.by_role(),
    )


def build_distill_model(config: Any) -> Any:
    """Build memory_model when configured, otherwise the main model."""

    from phone_agent.v2.model import build_chat_model

    name = getattr(config, "memory_model", None)
    active_config = replace(config, model_name=name) if name else config
    return build_chat_model(active_config)


def approve_if_eligible(
    store: LessonStore,
    lesson_id: str,
    episodes: Sequence[Mapping[str, Any]],
) -> LessonCandidate:
    """Approve one human-selected proposal only after Rule-of-3 passes."""

    candidate = store.get(lesson_id)
    if candidate is None:
        raise KeyError(f"unknown lesson: {lesson_id}")
    evaluation = evaluate_promotion(
        candidate,
        episodes,
        approved_lessons=store.lessons(status="approved"),
    )
    if not evaluation.eligible:
        raise ValueError("promotion blocked: " + ", ".join(evaluation.reasons))
    return store.approve(lesson_id)


def read_episode_outcomes(
    experience_dir: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    return _episode_rows(Path(experience_dir) / "events.jsonl")


__all__ = [
    "DistillResult",
    "LESSON_EVENT_TYPES",
    "LESSON_FIELDS",
    "LessonCandidate",
    "LessonStore",
    "PromotionEvaluation",
    "approve_if_eligible",
    "build_distill_model",
    "distill_lessons",
    "evaluate_promotion",
    "load_lessons",
    "make_lesson_id",
    "read_episode_outcomes",
    "select_lessons_for_injection",
]
