"""Capability metadata registry for the v2 thin-loop assembly.

The registry is an architectural data plane: it describes which capabilities
are configured for a run, but does not route tools or change capability
behaviour.  Optional lifecycle hooks reserve the plugin-kernel contract for
future effectful capabilities.  An ``apply`` must be paired with a ``release``
that leaves no residual effect; the initial passive capabilities use neither.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

CapabilityHook = Callable[[], None]
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class CapabilitySpec:
    """Stable identity, configured mode, dependencies, and lifecycle hooks."""

    cap_id: str
    title: str
    mode: str
    deps: tuple[str, ...] = ()
    apply: CapabilityHook | None = None
    release: CapabilityHook | None = None

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID.fullmatch(self.cap_id):
            raise ValueError(f"invalid capability id: {self.cap_id!r}")
        if not str(self.title).strip():
            raise ValueError("capability title must not be empty")
        if not str(self.mode).strip():
            raise ValueError("capability mode must not be empty")
        for dependency in self.deps:
            if not _CAPABILITY_ID.fullmatch(dependency):
                raise ValueError(f"invalid dependency id: {dependency!r}")
        if self.apply is not None and not callable(self.apply):
            raise TypeError("capability apply hook must be callable")
        if self.release is not None and not callable(self.release):
            raise TypeError("capability release hook must be callable")


class CapabilityRegistry:
    """Insertion-ordered registry with fail-visible dependency status."""

    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        """Register one stable id, rejecting accidental replacement."""

        if not isinstance(spec, CapabilitySpec):
            raise TypeError("spec must be a CapabilitySpec")
        if spec.cap_id in self._specs:
            raise ValueError(f"capability already registered: {spec.cap_id}")
        self._specs[spec.cap_id] = spec

    def status(self) -> list[dict[str, Any]]:
        """Return deterministic status rows without applying any effects.

        ``off`` takes precedence for the capability itself.  A non-off
        capability is ``pending`` when an immediate dependency is absent, off,
        or itself pending.  A ready ``shadow`` mode remains visible as shadow;
        every other ready non-off mode is active.
        """

        states: dict[str, str] = {}

        def resolve(cap_id: str, resolving: frozenset[str] = frozenset()) -> str:
            if cap_id in states:
                return states[cap_id]
            spec = self._specs.get(cap_id)
            if spec is None or cap_id in resolving:
                return "pending"
            mode = str(spec.mode).strip().lower()
            if mode == "off":
                states[cap_id] = "off"
                return "off"
            nested = resolving | {cap_id}
            missing = [
                dependency
                for dependency in spec.deps
                if resolve(dependency, nested) in {"off", "pending"}
            ]
            state = "pending" if missing else "shadow" if mode == "shadow" else "active"
            states[cap_id] = state
            return state

        rows: list[dict[str, Any]] = []
        for cap_id, spec in self._specs.items():
            state = resolve(cap_id)
            missing_deps = [
                dependency
                for dependency in spec.deps
                if resolve(dependency, frozenset({cap_id})) in {"off", "pending"}
            ]
            rows.append(
                {
                    "cap_id": cap_id,
                    "title": spec.title,
                    "mode": str(spec.mode).strip().lower(),
                    "state": state,
                    "missing_deps": missing_deps if state == "pending" else [],
                }
            )
        return rows


def build_capability_registry(config: Any) -> CapabilityRegistry:
    """Build the passive capability composition shared by agent and runner."""

    registry = CapabilityRegistry()
    for spec in (
        CapabilitySpec(
            "taskdoc",
            "TaskDoc",
            "on" if getattr(config, "taskdoc_enabled", True) else "off",
        ),
        CapabilitySpec("safety", "Safety", getattr(config, "safety_mode", "wary")),
        CapabilitySpec("budget", "Token budget", "on"),
        CapabilitySpec(
            "compact",
            "Auto compact",
            "on" if getattr(config, "compact_enabled", True) else "off",
        ),
        CapabilitySpec(
            "finish_verify",
            "Finish verifier",
            getattr(config, "finish_verify", "auto"),
        ),
        CapabilitySpec(
            "app_kb",
            "App knowledge",
            "on" if getattr(config, "app_kb_enabled", True) else "off",
        ),
        CapabilitySpec(
            "dream",
            "Memory maintenance",
            getattr(config, "dream_mode", "manual"),
            deps=("app_kb",),
        ),
        CapabilitySpec(
            "experience",
            "Experience plane",
            "on" if getattr(config, "experience_enabled", True) else "off",
        ),
        CapabilitySpec(
            "recall",
            "Memory recall",
            getattr(config, "memory_rag", "shadow"),
            deps=("experience",),
        ),
    ):
        registry.register(spec)
    return registry


__all__ = ["CapabilityRegistry", "CapabilitySpec", "build_capability_registry"]
