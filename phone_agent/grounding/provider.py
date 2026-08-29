"""Mark provider contracts for screen-bound target localization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from phone_agent.config.redact import sanitize_context_payload


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
    structure_topology_digest: str | None = None
    object_set_version: str | None = None

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
    password: bool = False
    editable: bool = False
    # Observation batch this mark belongs to (U1 batch-badge). Providers emit 0;
    # ``PhoneSession`` stamps the current batch number when it mints the mark's
    # external badged id (``ax_1@e12``). ``resolve_mark`` uses it as the freshness
    # gate — a mark whose ``epoch`` is not the session's current batch is stale.
    epoch: int = 0

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
    screen_structures: list[dict[str, Any]] = field(default_factory=list)
    screen_structure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = _sanitize_metadata(data.get("metadata"))
        for key in ("marks", "candidates"):
            for item in data.get(key) or []:
                if isinstance(item, dict) and item.get("text_summary"):
                    item["text_summary"] = sanitize_context_payload(
                        item.get("text_summary"),
                        "text_summary",
                        consumer="checkpoint",
                    )
        data["screen_structures"] = _sanitize_structures(data.get("screen_structures"))
        if isinstance(data.get("screen_structure"), dict):
            data["screen_structure"] = _sanitize_structure(data["screen_structure"])
        if data["screen_structures"]:
            # screen_structures is the canonical wire field.  Keep the legacy
            # alias only for old consumers that still read a single sidecar.
            data["screen_structure"] = data["screen_structures"][0]
        elif data["screen_structure"]:
            data["screen_structures"] = [data["screen_structure"]]
        return data


def _sanitize_structures(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_sanitize_structure(item) for item in value if isinstance(item, dict)]


def _sanitize_structure(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(value)
    nodes = sanitized.get("nodes")
    if isinstance(nodes, dict):
        sanitized_nodes: dict[str, Any] = {}
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            safe_node = dict(node)
            for key in ("text_summary", "content_desc_summary", "label", "title"):
                if safe_node.get(key):
                    safe_node[key] = sanitize_context_payload(
                        safe_node.get(key),
                        key,
                        consumer="checkpoint",
                    )
            sanitized_nodes[str(node_id)] = safe_node
        sanitized["nodes"] = sanitized_nodes
    return sanitized


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_metadata(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return sanitize_context_payload(value, "message", consumer="checkpoint")
    return value


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
