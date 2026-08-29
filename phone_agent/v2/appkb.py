"""Persistent app-name knowledge for the v2 phone-agent resolver.

The event log is authoritative. The materialized JSON file is a deterministic
view rebuilt from valid events at load time and refreshed after every mutation.
No model or device dependency belongs in this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unicodedata
from typing import Any

APP_KINDS = frozenset({"device", "alias", "learned", "user"})


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _iso_now() -> str:
    """Return the current time in ISO-8601 form."""

    return _utc_now().isoformat()


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing Z."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_term(value: str) -> str:
    """Case-fold a term after NFKC normalization and whitespace removal."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(normalized.split()).casefold()


def _entry_key(entry: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Return the storage identity for one knowledge entry."""

    return (
        str(entry["term"]),
        str(entry["package"]),
        str(entry["kind"]),
        str(entry["scope"]),
    )


def _sort_key(entry: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Return a stable order for persisted and returned entries."""

    return (
        str(entry["scope"]),
        str(entry["kind"]),
        _normalize_term(str(entry["term"])),
        str(entry["package"]),
    )


def _coerce_entry(
    entry: Mapping[str, Any], *, now: str | None = None
) -> dict[str, Any]:
    """Validate and copy one public entry into its canonical storage shape."""

    if not isinstance(entry, Mapping):
        raise TypeError("entry must be a mapping")

    term = str(entry.get("term", "")).strip()
    label = str(entry.get("label", "")).strip()
    package = str(entry.get("package", "")).strip()
    kind = str(entry.get("kind", "")).strip()
    scope = str(entry.get("scope", "")).strip()
    if not term:
        raise ValueError("entry.term must not be empty")
    if not label:
        raise ValueError("entry.label must not be empty")
    if not package:
        raise ValueError("entry.package must not be empty")
    if kind not in APP_KINDS:
        raise ValueError(f"entry.kind must be one of {sorted(APP_KINDS)!r}")
    if scope != "global" and not (
        scope.startswith("device:") and scope.removeprefix("device:").strip()
    ):
        raise ValueError("entry.scope must be 'global' or 'device:<serial>'")

    try:
        confidence = float(entry.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("entry.confidence must be a float") from exc
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("entry.confidence must be between 0.0 and 1.0")

    success_raw = entry.get("success_count", 0)
    if not isinstance(success_raw, int) or isinstance(success_raw, bool):
        raise ValueError("entry.success_count must be a non-negative integer")
    success_count = success_raw
    if success_count < 0:
        raise ValueError("entry.success_count must be a non-negative integer")

    stale = entry.get("stale", False)
    if not isinstance(stale, bool):
        raise ValueError("entry.stale must be a boolean")

    first_seen_raw = entry.get("first_seen")
    last_seen_raw = entry.get("last_seen")
    if first_seen_raw is None or last_seen_raw is None:
        timestamp = now or _iso_now()
        first_seen_raw = timestamp if first_seen_raw is None else first_seen_raw
        last_seen_raw = timestamp if last_seen_raw is None else last_seen_raw
    first_seen = str(first_seen_raw).strip()
    last_seen = str(last_seen_raw).strip()
    try:
        _parse_iso(first_seen)
        _parse_iso(last_seen)
    except (TypeError, ValueError) as exc:
        raise ValueError("entry timestamps must be valid ISO-8601 strings") from exc

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


def should_save(kind: str, *, durable: bool, sensitive: bool) -> bool:
    """Return whether durable, applicable, non-sensitive knowledge may persist.

    Unknown kinds fail closed. Callers must affirm durability explicitly; a
    transient observation or uncertain classification is not long-term memory.
    """

    return durable is True and sensitive is False and kind in APP_KINDS


class AppKnowledgeStore:
    """Append-only App-KB store with a materialized current view."""

    def __init__(self, memory_dir: str) -> None:
        self.memory_dir = Path(memory_dir)
        self.root = self.memory_dir / "app_kb"
        self.events_path = self.root / "events.jsonl"
        self.kb_path = self.root / "kb.json"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        self._entries = self._replay_events()
        self._write_materialized()

    def upsert(self, entry: Mapping[str, Any]) -> None:
        """Insert or replace one entry and append its mutation event."""

        candidate = _coerce_entry(entry)
        key = _entry_key(candidate)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                candidate["first_seen"] = min(
                    (existing["first_seen"], candidate["first_seen"]),
                    key=_parse_iso,
                )
            self._entries[key] = candidate
            self._append_event("upsert", candidate)
            self._write_materialized()

    def mark_stale(self, term: str, package: str | None = None) -> None:
        """Mark matching entries stale, appending one event per changed entry."""

        wanted_term = str(term)
        wanted_package = None if package is None else str(package)
        with self._lock:
            matches = [
                (key, entry)
                for key, entry in self._entries.items()
                if entry["term"] == wanted_term
                and (wanted_package is None or entry["package"] == wanted_package)
                and not entry["stale"]
            ]
            for key, entry in matches:
                updated = dict(entry)
                updated["stale"] = True
                self._entries[key] = updated
                self._append_event("mark_stale", updated)
            if matches:
                self._write_materialized()

    def delete(self, term: str, package: str) -> None:
        """Delete matching entries, appending one event per removed entry."""

        wanted_term = str(term)
        wanted_package = str(package)
        with self._lock:
            matches = [
                (key, entry)
                for key, entry in self._entries.items()
                if entry["term"] == wanted_term and entry["package"] == wanted_package
            ]
            for key, entry in matches:
                self._entries.pop(key, None)
                self._append_event("delete", entry)
            if matches:
                self._write_materialized()

    def entries(
        self,
        scope: str | None = None,
        kind: str | None = None,
        include_stale: bool = False,
    ) -> list[dict[str, Any]]:
        """Return copied entries matching optional scope and kind filters."""

        with self._lock:
            result = [
                dict(entry)
                for entry in self._entries.values()
                if (scope is None or entry["scope"] == scope)
                and (kind is None or entry["kind"] == kind)
                and (include_stale or not entry["stale"])
            ]
        return sorted(result, key=_sort_key)

    def sync_device(self, device_id: str, labels: list[tuple[str, str]]) -> None:
        """Upsert one device inventory snapshot without performing device I/O."""

        serial = str(device_id or "").strip()
        if not serial:
            raise ValueError("device_id must not be empty")
        timestamp = _iso_now()
        scope = f"device:{serial}"
        for package_value, label_value in labels:
            package = str(package_value or "").strip()
            label = str(label_value or "").strip() or package
            if not package:
                continue
            key = (label, package, "device", scope)
            with self._lock:
                existing = self._entries.get(key)
                first_seen = existing["first_seen"] if existing else timestamp
                success_count = existing["success_count"] if existing else 0
            self.upsert(
                {
                    "term": label,
                    "label": label,
                    "package": package,
                    "kind": "device",
                    "scope": scope,
                    "confidence": 1.0,
                    "success_count": success_count,
                    "first_seen": first_seen,
                    "last_seen": timestamp,
                    "stale": False,
                }
            )

    def _replay_events(self) -> dict[tuple[str, str, str, str], dict[str, Any]]:
        """Replay valid events, skipping malformed or unknown lines."""

        current: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    op = event["op"]
                    entry = _coerce_entry(event["entry"])
                    key = _entry_key(entry)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if op in {"upsert", "mark_stale"}:
                    current[key] = entry
                elif op == "delete":
                    current.pop(key, None)
        return current

    def _append_event(self, op: str, entry: Mapping[str, Any]) -> None:
        """Append and flush one mutation event."""

        event = {"op": op, "entry": dict(entry), "ts": _iso_now()}
        needs_newline = False
        if self.events_path.stat().st_size:
            with self.events_path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                needs_newline = stream.read(1) != b"\n"
        with self.events_path.open("a", encoding="utf-8") as stream:
            if needs_newline:
                stream.write("\n")
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_materialized(self) -> None:
        """Atomically rewrite kb.json from the in-memory projection."""

        payload = json.dumps(
            sorted(self._entries.values(), key=_sort_key),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(self.kb_path, payload + "\n")

    def _compact(self, entries: Sequence[Mapping[str, Any]], *, timestamp: str) -> None:
        """Replace both files with one snapshot event per surviving entry."""

        canonical = [_coerce_entry(entry, now=timestamp) for entry in entries]
        projection = {_entry_key(entry): entry for entry in canonical}
        ordered = sorted(projection.values(), key=_sort_key)
        events = "".join(
            json.dumps(
                {"op": "upsert", "entry": entry, "ts": timestamp},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for entry in ordered
        )
        materialized = (
            json.dumps(ordered, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        with self._lock:
            _atomic_write_text(self.events_path, events)
            _atomic_write_text(self.kb_path, materialized)
            self._entries = projection


class AppKnowledge:
    """Resolver-facing read view over applicable non-stale App-KB entries."""

    def __init__(
        self,
        store: AppKnowledgeStore | str | Path,
        device_id: str | None = None,
    ) -> None:
        self.store = (
            store
            if isinstance(store, AppKnowledgeStore)
            else AppKnowledgeStore(str(store))
        )
        self.device_id = None if device_id is None else str(device_id)

    def lookup(self, term: str) -> str | None:
        """Resolve a term through exact, normalized, substring, then alias tiers."""

        query = str(term or "")
        if not query:
            return None
        applicable = self._applicable_entries()
        direct = [entry for entry in applicable if entry["kind"] != "alias"]
        matched = self._match_direct(query, direct)
        if matched is not None:
            return matched["package"]
        return self._lookup_alias(query, applicable)

    def snapshot(self) -> dict[str, str]:
        """Return the currently resolvable term-to-package mapping."""

        terms = sorted({entry["term"] for entry in self._applicable_entries()})
        return {
            term: package
            for term in terms
            if (package := self.lookup(term)) is not None
        }

    def _applicable_entries(self) -> list[dict[str, Any]]:
        """Return global entries plus entries for this view's device."""

        device_scope = (
            f"device:{self.device_id}" if self.device_id is not None else None
        )
        return [
            entry
            for entry in self.store.entries(include_stale=False)
            if entry["scope"] == "global"
            or (device_scope is not None and entry["scope"] == device_scope)
        ]

    def _match_direct(
        self, query: str, entries: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Apply the three direct matching tiers to non-alias entries."""

        exact = [entry for entry in entries if entry["term"] == query]
        if exact:
            return self._best(exact)

        normalized_query = _normalize_term(query)
        if not normalized_query:
            return None
        normalized = [
            entry
            for entry in entries
            if _normalize_term(entry["term"]) == normalized_query
        ]
        if normalized:
            return self._best(normalized)

        substring = [
            entry
            for entry in entries
            if (candidate := _normalize_term(entry["term"]))
            and (candidate in normalized_query or normalized_query in candidate)
        ]
        return self._best(substring, prefer_long_term=True) if substring else None

    def _lookup_alias(self, query: str, applicable: list[dict[str, Any]]) -> str | None:
        """Resolve a global alias through an applicable label or package entry."""

        aliases = [
            entry
            for entry in applicable
            if entry["kind"] == "alias" and entry["scope"] == "global"
        ]
        alias = self._match_direct(query, aliases)
        if alias is None:
            return None

        normalized_label = _normalize_term(alias["label"])
        bridged = [
            entry
            for entry in applicable
            if entry["kind"] != "alias"
            and (
                entry["package"] == alias["package"]
                or _normalize_term(entry["label"]) == normalized_label
                or _normalize_term(entry["term"]) == normalized_label
            )
        ]
        return self._best(bridged)["package"] if bridged else alias["package"]

    def _best(
        self,
        entries: list[dict[str, Any]],
        *,
        prefer_long_term: bool = False,
    ) -> dict[str, Any]:
        """Choose a deterministic strongest candidate within one match tier."""

        device_scope = (
            f"device:{self.device_id}" if self.device_id is not None else None
        )

        def rank(entry: dict[str, Any]) -> tuple[int, int, float, int, datetime, str]:
            return (
                len(_normalize_term(entry["term"])) if prefer_long_term else 0,
                int(entry["scope"] == device_scope),
                float(entry["confidence"]),
                int(entry["success_count"]),
                _parse_iso(entry["last_seen"]),
                entry["package"],
            )

        return max(entries, key=rank)


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file in its existing directory."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
