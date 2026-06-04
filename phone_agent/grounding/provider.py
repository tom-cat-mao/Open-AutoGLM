"""Grounding provider contract for screen-bound target localization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class GroundingTarget:
    """Privacy-aware target description supplied by the planner."""

    text_hint: str | None = None
    role: str | None = None
    intent: str | None = None
    action: str | None = None
    requires_grounding: bool = True

    def description(self) -> str:
        parts = [self.role, self.text_hint, self.intent]
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "has_text_hint": bool(self.text_hint),
            "text_hint_length": len(self.text_hint or ""),
            "role": self.role,
            "intent": self.intent,
            "action": self.action,
            "requires_grounding": self.requires_grounding,
        }


@dataclass(frozen=True)
class ScreenBinding:
    """Run-local screen binding metadata for one grounding request."""

    screen_id: str
    raw_screenshot_hash: str
    width: int
    height: int
    current_app: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GroundingResult:
    """Provider result in 0-1000 screen-relative coordinates."""

    success: bool
    provider: str
    bbox: list[int] | None = None
    center: list[int] | None = None
    confidence: float | None = None
    failure_code: str | None = None
    message: str | None = None
    screen_id: str | None = None
    raw_screenshot_hash: str | None = None
    provider_input_hash: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GroundingProvider(Protocol):
    """Contract implemented by local or test grounding providers."""

    name: str
    version: str

    def ground(
        self,
        screenshot: Any,
        target: GroundingTarget,
        screen_binding: ScreenBinding,
        timeout: float | None = None,
    ) -> GroundingResult:
        """Locate target on screenshot and return a screen-bound result."""

