"""Node-local observation context for synchronous visual fact collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phone_agent.graph.observation import Observation


@dataclass(slots=True)
class RuntimeObservationContext:
    """Own a current screenshot without exposing a persistence serializer."""

    screenshot: Any = field(repr=False)
    observation: Observation
    screen_id: str
    observation_epoch: int
    _valid: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        snapshot = self.observation.snapshot
        if snapshot.screen_id != self.screen_id:
            raise ValueError("runtime context screen binding mismatch")
        if snapshot.observation_epoch != self.observation_epoch:
            raise ValueError("runtime context epoch binding mismatch")
        if self.screenshot is None:
            raise ValueError("runtime context requires the current screenshot")

    def require_current(self, *, screen_id: str, observation_epoch: int) -> None:
        """Fail closed when a provider attempts to use a stale context."""

        if (
            not self._valid
            or screen_id != self.screen_id
            or observation_epoch != self.observation_epoch
        ):
            raise RuntimeError("runtime observation context is stale")

    def invalidate(self) -> None:
        """Invalidate the screenshot as soon as a new observation is sampled."""

        self._valid = False
        self.screenshot = None

    def __getstate__(self) -> None:
        raise TypeError("RuntimeObservationContext is node-local and not serializable")
