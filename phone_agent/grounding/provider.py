"""Mark provider contracts for screen-bound target localization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MarkProviderHint:
    """Privacy-aware hint supplied to query-conditioned mark providers."""

    text: str
    source: str = "task"
    role: str | None = None
    intent: str | None = None
    action: str | None = None

    def description(self) -> str:
        parts = [self.role, self.text, self.intent]
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "has_text": bool(self.text),
            "text_length": len(self.text or ""),
            "has_role": bool(self.role),
            "role_length": len(self.role or ""),
            "has_intent": bool(self.intent),
            "intent_length": len(self.intent or ""),
            "action": self.action,
        }


@dataclass(frozen=True)
class ScreenBinding:
    """Run-local screen binding metadata for one grounding request."""

    screen_id: str
    raw_screenshot_hash: str
    width: int
    height: int
    current_app: str | None = None
    semantic_screen_id: str | None = None
    observation_epoch: int = 0
    mark_set_version: str | None = None
    perceptual_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarkCandidate:
    """One provider mark candidate in normalized 0-1000 coordinates."""

    mark_id: str
    bbox: list[int]
    center: list[int]
    confidence: float | None = None
    source: str | None = None
    valid: bool = True
    reason: str | None = None
    role: str | None = None
    text_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarkProviderResult:
    """Provider result containing screen-bound mark candidates."""

    success: bool
    provider: str
    failure_code: str | None = None
    message: str | None = None
    screen_id: str | None = None
    raw_screenshot_hash: str | None = None
    provider_input_hash: str | None = None
    latency_ms: int | None = None
    marks: list[MarkCandidate] = field(default_factory=list)
    candidates: list[MarkCandidate] = field(default_factory=list)
    candidate_count: int = 0
    status: str | None = None
    hints: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarkProvider(Protocol):
    """Contract implemented by local or test mark providers."""

    name: str
    version: str

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
    ) -> MarkProviderResult:
        """Return screen-bound mark candidates for the current observation."""
