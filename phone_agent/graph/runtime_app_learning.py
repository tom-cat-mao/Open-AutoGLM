"""Per-run, nonserializable learned app term -> package mapping cache.

The device is the fact source for app launch; the static registry is an alias
seed. When a Launch succeeds through the device-inventory or candidate path,
the mapping (normalized user term -> actual package name) is recorded here for
the rest of the run. It is a runtime container (never checkpointed/serialized),
mirroring RuntimeGoalContext's per-invocation dependency role.
"""

from __future__ import annotations

from phone_agent.config.app_registry import normalize_app_term


class RuntimeAppLearningContext:
    """Per-run learned launch mappings; never serialized."""

    def __init__(self) -> None:
        self._mapping: dict[str, str] = {}

    def record(self, term: str, package_name: str) -> None:
        """Learn that a normalized user term launches the given package."""

        normalized = normalize_app_term(term)
        package = str(package_name or "").strip()
        if normalized and package:
            self._mapping[normalized] = package

    def lookup(self, term: str) -> str | None:
        """Return the learned package for a term, or None."""

        return self._mapping.get(normalize_app_term(term))

    def snapshot(self) -> dict[str, str]:
        """Return a sorted snapshot for prompt/trace consumption."""

        return dict(sorted(self._mapping.items()))

    def __getstate__(self) -> dict:
        raise TypeError("RuntimeAppLearningContext is per-run and not serializable")
