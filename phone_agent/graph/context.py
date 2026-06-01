"""Short-term context helpers for graph observability and plan injection."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CONTEXT_MODES = {"off", "observe", "inject"}
DEFAULT_CONTEXT_MODE = "observe"
DEFAULT_CONTEXT_BUDGET: dict[str, int] = {
    "screen_belief_summary_chars": 300,
    "summarized_history_chars": 800,
    "failure_memory_items": 3,
    "action_outcome_items": 1,
    "context_block_chars": 1500,
}
FAILURE_TAXONOMY = {
    "none",
    "element_not_found",
    "wrong_page",
    "app_not_responding",
    "network_or_loading",
    "permission_or_login_or_captcha",
    "unsafe_or_sensitive",
    "coordinate_or_tap_offset",
    "context_lost",
    "repeated_action",
    "model_parse_failed",
    "unknown",
}
FAILURE_CAUSE_ALIASES = {
    "app_not_responding_or_loading": "app_not_responding",
    "permission_login_captcha": "permission_or_login_or_captcha",
    "unsafe_or_sensitive_hitl": "unsafe_or_sensitive",
    "coordinate_or_click_offset": "coordinate_or_tap_offset",
    "network": "network_or_loading",
}
PRIVATE_CONTEXT_TEXT_KEYS = {
    "visible_text",
    "observed_text",
    "raw_text",
    "chat_content",
    "address",
    "captcha",
    "verification_code",
    "account",
    "payment_info",
    "summary",
    "message",
    "result_message_summary",
    "final_message",
    "error",
    "reflection",
}
SENSITIVE_PATTERN = re.compile(
    r"(1[3-9]\d{9}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:订单|order)[\s:#：-]*[A-Za-z0-9-]{4,}|"
    r"(?:验证码|code)[\s:#：-]*\d{4,8}|(?:api[_-]?key|token|secret)[\s:=：]+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9._-]+|Bearer\s+[A-Za-z0-9._-]+|eyJ[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+|"
    r"[\u4e00-\u9fff]{2,4}|[A-Za-z0-9+/]{80,}={0,2})",
    re.IGNORECASE,
)


def normalize_context_mode(value: str | None) -> str:
    """Normalize context mode to off/observe/inject."""
    mode = (value or DEFAULT_CONTEXT_MODE).strip().lower()
    if mode not in CONTEXT_MODES:
        return DEFAULT_CONTEXT_MODE
    return mode


def get_context_mode(state: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    """Read context mode from graph config, then state, then default."""
    configurable = config.get("configurable", {}) if config else {}
    return normalize_context_mode(configurable.get("context_mode") or state.get("context_mode"))


def context_enabled(mode: str) -> bool:
    """Return whether context collection is enabled."""
    return normalize_context_mode(mode) != "off"


def should_inject_context(mode: str) -> bool:
    """Return whether plan context injection is enabled."""
    return normalize_context_mode(mode) == "inject"


def default_screen_belief() -> dict[str, Any]:
    """Build the default conservative screen belief."""
    return {
        "current_app": "",
        "summary": "unknown",
        "loading_or_blocked": False,
        "unsafe_or_sensitive": False,
        "confidence": "unknown",
        "updated_step": 0,
    }


def default_context_budget() -> dict[str, int]:
    """Return a copy of the default context budget."""
    return dict(DEFAULT_CONTEXT_BUDGET)


def normalize_failure_cause(value: str | None) -> str:
    """Normalize failure cause labels to the canonical taxonomy."""
    cause = (value or "unknown").strip().lower()
    cause = FAILURE_CAUSE_ALIASES.get(cause, cause)
    if cause not in FAILURE_TAXONOMY:
        return "unknown"
    return cause


def redact_context_text(text: str | None) -> str:
    """Redact identifiable UI text before state/trace/eval/prompt use."""
    if not text:
        return ""
    if SENSITIVE_PATTERN.search(str(text)):
        return "<redacted>"
    return str(text)


def _redacted_private_text(text: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }


def sanitize_context_payload(payload: Any, key: str | None = None) -> Any:
    """Recursively sanitize context payload independent of trace key names."""
    normalized_key = (key or "").lower()
    if isinstance(payload, str):
        if normalized_key in PRIVATE_CONTEXT_TEXT_KEYS:
            return _redacted_private_text(payload)
        return redact_context_text(payload)
    if isinstance(payload, dict):
        return {str(k): sanitize_context_payload(v, str(k)) for k, v in payload.items()}
    if isinstance(payload, list):
        return [sanitize_context_payload(item, key) for item in payload]
    if isinstance(payload, tuple):
        return [sanitize_context_payload(item, key) for item in payload]
    return payload


def trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim text and return whether truncation happened."""
    if len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 20)] + "...<truncated>", True


def build_screen_belief(
    *, current_app: str, step_count: int = 0, summary: str | None = None
) -> dict[str, Any]:
    """Build a conservative short-term screen belief."""
    safe_summary = "unknown" if not summary else redact_context_text(summary)
    safe_summary, _ = trim_text(safe_summary, DEFAULT_CONTEXT_BUDGET["screen_belief_summary_chars"])
    return {
        "current_app": current_app or "unknown",
        "summary": safe_summary or "unknown",
        "loading_or_blocked": False,
        "unsafe_or_sensitive": False,
        "confidence": "unknown",
        "updated_step": step_count,
    }


def build_action_outcome_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Build a sanitized action outcome summary from graph state."""
    action = state.get("action_parsed") or {}
    result = state.get("action_result") or {}
    return sanitize_context_payload(
        {
            "step_count": int(state.get("step_count") or 0),
            "action": action.get("action") if isinstance(action, dict) else None,
            "action_metadata": action.get("_metadata") if isinstance(action, dict) else None,
            "execution_success": result.get("success") if isinstance(result, dict) else None,
            "should_finish": result.get("should_finish") if isinstance(result, dict) else None,
            "result_message_summary": result.get("message") if isinstance(result, dict) else None,
            "current_app": state.get("current_app") or "unknown",
            "reflection_verdict": state.get("reflection_verdict"),
            "failure_cause": state.get("failure_cause"),
            "suggested_strategy": state.get("suggested_strategy"),
        }
    )


def is_failed_outcome(outcome: dict[str, Any]) -> bool:
    """Return whether outcome should enter failure memory."""
    if outcome.get("reflection_verdict") in {"failed", "partial"}:
        return True
    if outcome.get("failure_cause"):
        return True
    return outcome.get("execution_success") is False


def detect_repeated_failure(failure_memory: list[dict[str, Any]], outcome: dict[str, Any]) -> bool:
    """Detect repeated failures by action/cause/app tuple."""
    if not is_failed_outcome(outcome):
        return False
    key = (outcome.get("action"), outcome.get("failure_cause"), outcome.get("current_app"))
    return any(
        (item.get("action"), item.get("failure_cause"), item.get("current_app")) == key
        for item in failure_memory
    )


def update_failure_memory(
    existing: list[dict[str, Any]], outcome: dict[str, Any], budget: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """Append failed outcome and keep the recent bounded window."""
    if not is_failed_outcome(outcome):
        return list(existing or [])
    active_budget = budget or DEFAULT_CONTEXT_BUDGET
    item = sanitize_context_payload(
        {
            "step_count": outcome.get("step_count"),
            "action": outcome.get("action"),
            "current_app": outcome.get("current_app"),
            "failure_cause": outcome.get("failure_cause") or "unknown",
            "suggested_strategy": outcome.get("suggested_strategy"),
        }
    )
    return (list(existing or []) + [item])[-active_budget["failure_memory_items"] :]


def update_summarized_history(
    previous: str, outcome: dict[str, Any], budget: dict[str, int] | None = None
) -> tuple[str, bool]:
    """Append one sanitized history line and enforce history budget."""
    active_budget = budget or DEFAULT_CONTEXT_BUDGET
    line = (
        f"step={outcome.get('step_count')} action={outcome.get('action')} "
        f"success={outcome.get('execution_success')} verdict={outcome.get('reflection_verdict')} "
        f"cause={outcome.get('failure_cause') or 'none'} strategy={outcome.get('suggested_strategy') or 'none'}"
    )
    combined = "\n".join(part for part in [previous, redact_context_text(line)] if part)
    return trim_text(combined, active_budget["summarized_history_chars"])


def build_context_metrics(state: dict[str, Any]) -> dict[str, Any]:
    """Build comparable context metrics for RunResult/eval/trace."""
    return {
        "context_mode": normalize_context_mode(state.get("context_mode")),
        "context_block_chars": int(state.get("context_block_chars") or 0),
        "context_truncated": bool(state.get("context_truncated")),
        "failure_memory_hit_count": int(state.get("failure_memory_hit_count") or 0),
        "repeated_failure_count": int(state.get("repeated_failure_count") or 0),
    }


def build_plan_context_block(state: dict[str, Any], lang: str = "cn") -> tuple[str, dict[str, Any]]:
    """Build a bounded, sanitized context block for plan injection."""
    budget = state.get("context_budget") or DEFAULT_CONTEXT_BUDGET
    parts = []
    component_truncated = False
    title = "** Short-term Context (belief, not authorization) **"
    if lang != "en":
        title = "** 短期上下文（仅为信念，不代表授权） **"
    for label, value in (
        ("screen_belief", state.get("screen_belief")),
        ("last_action_outcome", state.get("action_outcome_summary")),
        ("latest_failure_memory", (state.get("failure_memory") or [])[-1:]),
        ("summarized_history", state.get("summarized_history")),
    ):
        if value:
            if label == "summarized_history" and len(str(value)) > budget["summarized_history_chars"]:
                component_truncated = True
            if label == "screen_belief" and isinstance(value, dict):
                summary = str(value.get("summary") or "")
                if len(summary) > budget["screen_belief_summary_chars"]:
                    component_truncated = True
            parts.append(f"{label}: {json.dumps(sanitize_context_payload(value), ensure_ascii=False)}")
    if not parts:
        return "", {"context_block_chars": 0, "context_truncated": False}
    block, truncated = trim_text(
        title + "\n" + "\n".join(parts), budget["context_block_chars"]
    )
    return block, {"context_block_chars": len(block), "context_truncated": truncated or component_truncated}
