"""Short-term context helpers for graph observability and plan injection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
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
DEFAULT_PROMPT_VERSION = "context_harness_v1"
CONTEXT_SECTION_IDS = (
    "screen_belief",
    "last_action_outcome",
    "failure_memory",
    "summarized_history",
)
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
    "text",
    "label",
    "value",
    "title",
    "subtitle",
    "address",
    "captcha",
    "verification_code",
    "account",
    "payment_info",
    "message",
    "result_message_summary",
    "final_message",
    "error",
    "reflection",
}
SAFE_CONTEXT_TEXT_KEYS = {
    "summary",
    "current_app",
    "confidence",
    "action",
    "action_metadata",
    "reflection_verdict",
    "failure_cause",
    "suggested_strategy",
    "summarized_history",
}
SENSITIVE_PATTERN = re.compile(
    r"(1[3-9]\d{9}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:订单|order)[\s:#：-]*[A-Za-z0-9-]{4,}|"
    r"(?:验证码|code)[\s:#：-]*\d{4,8}|(?:api[_-]?key|token|secret)[\s:=：]+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9._-]+|Bearer\s+[A-Za-z0-9._-]+|eyJ[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+|"
    r"[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹][\u4e00-\u9fff]{1,2}|"
    r"[A-Za-z0-9+/]{80,}={0,2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContextSelectionResult:
    """Trace-safe context selector output for one model request."""

    context_mode: str
    context_strategy: str
    prompt_version: str = DEFAULT_PROMPT_VERSION
    selected_sections: list[str] | None = None
    context_block: str = ""
    context_block_chars: int = 0
    context_truncated: bool = False
    messages_before: int = 0
    messages_after: int = 0
    message_chars_before: int = 0
    message_chars_after: int = 0
    approx_tokens_before: int = 0
    approx_tokens_after: int = 0

    def metrics(self, include_block: bool = False) -> dict[str, Any]:
        """Return a JSON-friendly, privacy-safe metrics dictionary."""
        data = asdict(self)
        if not include_block:
            data.pop("context_block", None)
        data["selected_sections"] = list(self.selected_sections or [])
        return data


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
        if normalized_key and normalized_key not in SAFE_CONTEXT_TEXT_KEYS:
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
        "context_strategy": state.get("context_strategy") or "unknown",
        "prompt_version": state.get("prompt_version") or DEFAULT_PROMPT_VERSION,
        "selected_sections": list(state.get("selected_sections") or []),
        "context_block_chars": int(state.get("context_block_chars") or 0),
        "context_truncated": bool(state.get("context_truncated")),
        "messages_before": int(state.get("messages_before") or 0),
        "messages_after": int(state.get("messages_after") or 0),
        "message_chars_before": int(state.get("message_chars_before") or 0),
        "message_chars_after": int(state.get("message_chars_after") or 0),
        "approx_tokens_before": int(state.get("approx_tokens_before") or 0),
        "approx_tokens_after": int(state.get("approx_tokens_after") or 0),
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


def select_plan_context(
    state: dict[str, Any], *, mode: str, lang: str = "cn", prompt_version: str | None = None
) -> ContextSelectionResult:
    """Select trace-safe context sections without mutating graph state."""
    normalized_mode = normalize_context_mode(mode)
    sections = [section for section in CONTEXT_SECTION_IDS if _section_has_value(state, section)]
    if normalized_mode == "off":
        return ContextSelectionResult(
            context_mode=normalized_mode,
            context_strategy="off",
            prompt_version=prompt_version or DEFAULT_PROMPT_VERSION,
            selected_sections=[],
        )
    if not should_inject_context(normalized_mode):
        return ContextSelectionResult(
            context_mode=normalized_mode,
            context_strategy="observe_only",
            prompt_version=prompt_version or DEFAULT_PROMPT_VERSION,
            selected_sections=sections,
        )
    block, metrics = build_plan_context_block(state, lang)
    return ContextSelectionResult(
        context_mode=normalized_mode,
        context_strategy="inject_redacted_block",
        prompt_version=prompt_version or DEFAULT_PROMPT_VERSION,
        selected_sections=sections,
        context_block=block,
        context_block_chars=int(metrics.get("context_block_chars") or 0),
        context_truncated=bool(metrics.get("context_truncated")),
    )


def compact_messages_for_request(
    messages: list[dict[str, Any]], selection: ContextSelectionResult
) -> tuple[list[dict[str, Any]], ContextSelectionResult]:
    """Compact request messages without mutating state messages.

    Historical images are stripped from every message except the latest user
    request. Text is preserved so action auditability remains intact.
    """
    before_chars = _messages_approx_chars(messages)
    compacted = [_compact_message(message, keep_images=False) for message in messages]
    latest_user_index = _latest_user_message_index(messages)
    if latest_user_index is not None:
        compacted[latest_user_index] = _compact_message(messages[latest_user_index], keep_images=True)
    after_chars = _messages_approx_chars(compacted)
    updated = ContextSelectionResult(
        **{
            **selection.metrics(include_block=True),
            "messages_before": len(messages),
            "messages_after": len(compacted),
            "message_chars_before": before_chars,
            "message_chars_after": after_chars,
            "approx_tokens_before": _approx_tokens(before_chars),
            "approx_tokens_after": _approx_tokens(after_chars),
        }
    )
    return compacted, updated


def _section_has_value(state: dict[str, Any], section: str) -> bool:
    if section == "screen_belief":
        return bool(state.get("screen_belief"))
    if section == "last_action_outcome":
        return bool(state.get("action_outcome_summary"))
    if section == "failure_memory":
        return bool(state.get("failure_memory"))
    if section == "summarized_history":
        return bool(state.get("summarized_history"))
    return False


def _latest_user_message_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _compact_message(message: dict[str, Any], *, keep_images: bool) -> dict[str, Any]:
    copied = dict(message)
    content = copied.get("content")
    if isinstance(content, list):
        copied["content"] = [
            dict(item)
            for item in content
            if keep_images or not (isinstance(item, dict) and item.get("type") == "image_url")
        ]
    return copied


def _messages_approx_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_value_approx_chars(message) for message in messages)


def _value_approx_chars(value: Any) -> int:
    if isinstance(value, str):
        if value.startswith("data:image") or len(value) > 2000:
            return min(len(value), 2000)
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(key)) + _value_approx_chars(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(_value_approx_chars(item) for item in value)
    return len(str(value))


def _approx_tokens(chars: int) -> int:
    return max(0, (int(chars) + 3) // 4)
