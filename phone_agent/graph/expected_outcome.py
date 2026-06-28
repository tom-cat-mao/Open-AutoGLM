"""Expected postcondition contract for action verification.

ExpectedOutcome is a sibling of ActionIR. It is produced from provider output
or conservative defaults, stored for reflection/trace, and never passed to the
executor or safety gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
from typing import Any, Literal

from phone_agent.graph.context import redact_context_text, sanitize_context_payload


OutcomeKind = Literal[
    "generic",
    "app_opened",
    "input_focused",
    "text_present",
    "page_opened",
    "content_shifted",
    "target_appeared",
    "loading_finished",
]

VALID_OUTCOME_KINDS: set[str] = {
    "generic",
    "app_opened",
    "input_focused",
    "text_present",
    "page_opened",
    "content_shifted",
    "target_appeared",
    "loading_finished",
}
EXPECTED_OUTCOME_FIELDS = {
    "kind",
    "must_observe",
    "must_not_observe",
    "target_mark_id",
    "target_text_hint",
    "timeout_hint",
    "dynamic_regions",
    "selected_object_id_hash",
    "object_type",
    "object_evidence_hash",
    "title_stub",
    "title_hash",
    "container_lineage_hash",
    "list_lineage_hash",
    "expected_page_type",
    "expected_rank",
}
EXPECTED_OBJECT_FIELDS = {
    "selected_object_id_hash",
    "object_type",
    "object_evidence_hash",
    "title_stub",
    "title_hash",
    "container_lineage_hash",
    "list_lineage_hash",
    "expected_page_type",
    "expected_rank",
}
MAX_LIST_ITEMS = 12
MAX_TEXT_CHARS = 160


@dataclass(frozen=True)
class ExpectedOutcome:
    """Trace-safe verification contract for the next reflect step."""

    kind: OutcomeKind = "generic"
    must_observe: list[str] = field(default_factory=list)
    must_not_observe: list[str] = field(default_factory=list)
    target_mark_id: str | None = None
    target_text_hint: str | None = None
    timeout_hint: float | None = None
    dynamic_regions: list[str] = field(default_factory=list)
    selected_object_id_hash: str | None = None
    object_type: str | None = None
    object_evidence_hash: str | None = None
    title_stub: str | None = None
    title_hash: str | None = None
    container_lineage_hash: str | None = None
    list_lineage_hash: str | None = None
    expected_page_type: str | None = None
    expected_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dictionary."""

        return asdict(self)

    def trace_summary(self, *, task_context: str | None = None) -> dict[str, Any]:
        """Return a redacted bounded summary safe for trace and prompt use."""

        return sanitize_context_payload(
            self.to_dict(),
            consumer="trace",
            task_context=task_context,
        )


def extract_provider_envelope(payload: Any) -> tuple[Any, dict[str, Any] | None]:
    """Split provider response into action payload and optional expected outcome.

    Supported envelope shape:

    {"action": {...}, "expected_outcome": {...}}

    Plain legacy action JSON remains fully compatible.
    """

    if not isinstance(payload, dict):
        return payload, None
    if "expected_outcome" not in payload:
        return payload, None
    action_payload = payload.get("action")
    if not isinstance(action_payload, dict):
        return payload, None
    raw_outcome = payload.get("expected_outcome")
    return action_payload, raw_outcome if isinstance(raw_outcome, dict) else None


def normalize_expected_outcome(
    raw: dict[str, Any] | None,
    *,
    action: dict[str, Any] | None = None,
    intent: dict[str, Any] | None = None,
    selected_object_evidence: dict[str, Any] | None = None,
) -> ExpectedOutcome:
    """Validate and normalize an expected outcome with conservative defaults."""

    default = default_expected_outcome(action=action, intent=intent)
    selected_fields = _normalize_selected_object_fields(selected_object_evidence or {})
    if selected_fields:
        default = _default_with_selected_object(default, selected_fields)
    if not isinstance(raw, dict):
        return default
    unsupported = set(raw) - EXPECTED_OUTCOME_FIELDS
    if unsupported:
        return default
    object_fields = _normalize_selected_object_fields(raw)
    if selected_fields:
        object_fields = {**object_fields, **selected_fields}

    kind = raw.get("kind", default.kind)
    if not isinstance(kind, str) or kind not in VALID_OUTCOME_KINDS:
        kind = default.kind

    return ExpectedOutcome(
        kind=kind,  # type: ignore[arg-type]
        must_observe=_normalize_string_list(raw.get("must_observe"), default.must_observe),
        must_not_observe=_normalize_string_list(raw.get("must_not_observe"), default.must_not_observe),
        target_mark_id=_normalize_optional_string(raw.get("target_mark_id"), default.target_mark_id),
        target_text_hint=_normalize_optional_string(raw.get("target_text_hint"), default.target_text_hint),
        timeout_hint=_normalize_timeout(raw.get("timeout_hint"), default.timeout_hint),
        dynamic_regions=_normalize_string_list(raw.get("dynamic_regions"), default.dynamic_regions),
        **object_fields,
    )


def sanitize_expected_outcome_dict(
    outcome: ExpectedOutcome | dict[str, Any] | None,
    *,
    task_context: str | None = None,
) -> dict[str, Any] | None:
    """Return a private-text-stubbed outcome dict for state/trace/result storage."""

    if outcome is None:
        return None
    if isinstance(outcome, ExpectedOutcome):
        raw = outcome.to_dict()
    elif isinstance(outcome, dict):
        raw = dict(outcome)
    else:
        return None
    if not isinstance(raw, dict):
        return None
    for key in ("must_observe", "must_not_observe", "dynamic_regions"):
        if key in raw:
            raw[key] = _stub_text_list(raw.get(key))
    if "target_text_hint" in raw:
        raw["target_text_hint"] = _stub_text_value(raw.get("target_text_hint"))
    for key in EXPECTED_OBJECT_FIELDS:
        if key in raw:
            raw[key] = _normalize_expected_object_value(key, raw.get(key))
    sanitized = sanitize_context_payload(raw, consumer="trace_payload", task_context=task_context)
    if not isinstance(sanitized, dict):
        return None
    return sanitized


def runtime_expected_outcome_dict(
    outcome: ExpectedOutcome | dict[str, Any] | None,
    *,
    task_context: str | None = None,
) -> dict[str, Any] | None:
    """Return the verifier/runtime contract without raw provider text.

    The verifier can match sha256 stubs against observed UI text on the fly.
    Regex-redacted private values become ``private_text_unverifiable``.
    """

    sanitized = sanitize_expected_outcome_dict(outcome, task_context=task_context)
    if sanitized is None:
        return None
    return normalize_expected_outcome(sanitized).to_dict()


def _stub_text_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_stub_text_value(item) for item in value if isinstance(item, str)]


def _stub_text_value(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    redacted = redact_context_text(value)
    if redacted != value:
        return {
            "redacted": True,
            "private_text_unverifiable": True,
            "length": len(value),
        }
    return {
        "redacted": True,
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }


def default_expected_outcome(
    *,
    action: dict[str, Any] | None,
    intent: dict[str, Any] | None = None,
) -> ExpectedOutcome:
    """Build a conservative default when the provider omits the contract."""

    if not isinstance(action, dict):
        return ExpectedOutcome()
    action_name = action.get("action")
    target_mark_id = None
    target_text_hint = None
    if isinstance(intent, dict):
        target_mark_id = _normalize_optional_string(intent.get("target_mark_id"), None)
        target_text_hint = _normalize_optional_string(intent.get("target_text_hint"), None)
    if action_name == "Launch":
        return ExpectedOutcome(
            kind="app_opened",
            must_observe=[str(action.get("app"))] if action.get("app") else [],
            target_text_hint=str(action.get("app")) if action.get("app") else None,
        )
    if action_name in {"Type", "Type_Name"}:
        return ExpectedOutcome(kind="text_present")
    if action_name in {"Tap", "Double Tap", "Long Press"}:
        return ExpectedOutcome(
            kind="generic",
            target_mark_id=target_mark_id,
            target_text_hint=target_text_hint,
            dynamic_regions=["ads", "banner", "recommendation_feed", "hot_words", "counters"],
        )
    if action_name == "Swipe":
        return ExpectedOutcome(kind="content_shifted", dynamic_regions=["ads", "banner", "hot_words"])
    if action_name == "Wait":
        return ExpectedOutcome(kind="loading_finished", must_not_observe=["loading", "spinner", "network_error"])
    return ExpectedOutcome()


def _default_with_selected_object(
    default: ExpectedOutcome,
    selected_fields: dict[str, str | None],
) -> ExpectedOutcome:
    kind = default.kind
    expected_page_type = selected_fields.get("expected_page_type")
    object_type = selected_fields.get("object_type")
    if expected_page_type == "input_focused" or object_type in {"input", "search", "textfield"}:
        kind = "input_focused"
    elif object_type in {"video", "card", "result", "item", "visual_card", "visual_target"}:
        kind = "page_opened"
    elif kind == "generic" and expected_page_type:
        kind = "target_appeared"
    return ExpectedOutcome(**{**default.to_dict(), "kind": kind, **selected_fields})


def _normalize_selected_object_fields(raw: dict[str, Any]) -> dict[str, str | int | None]:
    result: dict[str, str | int | None] = {}
    for key in EXPECTED_OBJECT_FIELDS:
        normalized = _normalize_expected_object_value(key, raw.get(key))
        if normalized is not None:
            result[key] = normalized
    return result


def _normalize_expected_object_value(key: str, value: Any) -> str | int | None:
    if value is None:
        return None
    if key == "expected_rank":
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100:
            return value
        if isinstance(value, str) and value.isdigit():
            rank = int(value)
            return rank if 1 <= rank <= 100 else None
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if key.endswith("_hash") or key.endswith("_id_hash"):
        return text[:64] if re.fullmatch(r"[0-9a-fA-F]{6,64}", text) else None
    if key == "title_stub":
        return text[:160] if re.fullmatch(r"len:\d{1,6} sha256:[0-9a-fA-F]{6,64}", text) else None
    return sanitize_context_payload(text, "message", consumer="trace_payload")[:64]


def expected_outcome_prompt_block(
    outcome: ExpectedOutcome | dict[str, Any] | None,
    *,
    lang: str,
    task_context: str | None = None,
) -> str:
    """Render a compact expected outcome block for verifier prompts."""

    summary = sanitize_expected_outcome_dict(outcome, task_context=task_context)
    if summary is None:
        summary = ExpectedOutcome().trace_summary(task_context=task_context)
    if lang == "en":
        return f"Expected postconditions (not execution authorization): {summary}"
    return f"预期后置条件（不是执行授权）：{summary}"


def _normalize_string_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result = []
    for item in value[:MAX_LIST_ITEMS]:
        if isinstance(item, str):
            text = item.strip()
            if text:
                if text in {"<redacted>", "<matches_task_value>"}:
                    result.append("private_text_unverifiable")
                    continue
                result.append(text[:MAX_TEXT_CHARS])
        elif isinstance(item, dict):
            if item.get("private_text_unverifiable") is True:
                result.append("private_text_unverifiable")
                continue
            digest = item.get("sha256")
            if isinstance(digest, str) and digest:
                result.append(f"sha256:{digest[:12]}")
    return result


def _normalize_optional_string(value: Any, default: str | None) -> str | None:
    if isinstance(value, dict):
        if value.get("private_text_unverifiable") is True:
            return "private_text_unverifiable"
        digest = value.get("sha256")
        if isinstance(digest, str) and digest:
            return f"sha256:{digest[:12]}"
    if value is None:
        return default
    if not isinstance(value, str):
        return default
    text = value.strip()
    if text in {"<redacted>", "<matches_task_value>"}:
        return "private_text_unverifiable"
    return text[:MAX_TEXT_CHARS] if text else default


def _normalize_timeout(value: Any, default: float | None) -> float | None:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    if value <= 0 or value > 60:
        return default
    return float(value)
