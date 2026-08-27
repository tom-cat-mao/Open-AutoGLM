"""Privacy redaction primitives extracted from the retired v1 graph context.

Single source of truth for egress/prompt-side text sanitization used by
grounding providers, trace, and the v2 middleware.
"""

from __future__ import annotations

import re
from typing import Any

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
    "text_hint",
    "target_text_hint",
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
    "screen_id",
    "mark_id",
    "provider",
    "provider_input_hash",
    "raw_screenshot_hash",
    "failure_code",
    "last_verdict",
    "sha256",
}

CONSUMER_POLICY: dict[str, str] = {
    "inject": "regex",
    "reflect_prompt": "regex",
    "trace_payload": "regex",
    "checkpoint": "stub",
    "default": "regex",
}
ContextConsumer = str

SENSITIVE_PATTERN = re.compile(
    r"(1[3-9]\d{9}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:订单|order)[\s:#：-]*[A-Za-z0-9-]{4,}|"
    r"(?:验证码|code)[\s:#：-]*\d{4,8}|(?:api[_-]?key|token|secret)[\s:=：]+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9._-]+|Bearer\s+[A-Za-z0-9._-]+|eyJ[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+|"
    r"[A-Za-z0-9+/]{120,}={0,2})",
    re.IGNORECASE,
)


def redact_context_text(text: str | None) -> str:
    """Replace sensitive patterns inline, preserving surrounding text."""
    if not text:
        return ""
    return SENSITIVE_PATTERN.sub("<redacted>", str(text))


sanitize_context_text_regex = redact_context_text


def _redacted_private_text(text: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(text),
    }


def _task_sensitive_values(task_context: str | None) -> tuple[str, ...]:
    if not task_context:
        return ()
    values = {match.group(0) for match in SENSITIVE_PATTERN.finditer(str(task_context))}
    return tuple(sorted(values, key=len, reverse=True))


def _mark_task_matches(text: str, task_values: tuple[str, ...]) -> str:
    marked = text
    for value in task_values:
        if value:
            marked = marked.replace(value, "<matches_task_value>")
    return marked


def _resolve_consumer(*, consumer: str | None, inject: bool | None) -> str:
    if consumer:
        return consumer
    if inject is True:
        return "inject"
    if inject is False:
        return "checkpoint"
    return "default"


def sanitize_context_payload(
    payload: Any,
    key: str | None = None,
    *,
    inject: bool | None = None,
    consumer: str | None = None,
    task_context: str | None = None,
) -> Any:
    """Recursively sanitize context payload per *consumer* policy.

    Consumer policy (see ``CONSUMER_POLICY``):

    * ``"inject"`` / ``"reflect_prompt"`` / ``"trace_payload"`` / default:
      every string is regex-redacted; key-level classification is ignored.
    * ``"checkpoint"``: private-text keys are replaced with a redaction stub
      (``{redacted, length}``); safe keys are regex-redacted.
    """
    resolved = _resolve_consumer(consumer=consumer, inject=inject)
    if resolved not in CONSUMER_POLICY:
        raise ValueError(f"unregistered context consumer: {resolved}")
    policy = CONSUMER_POLICY[resolved]
    task_values = (
        _task_sensitive_values(task_context)
        if resolved in {"inject", "reflect_prompt"}
        else ()
    )
    return _sanitize_payload_impl(payload, key, policy=policy, task_values=task_values)


def _sanitize_payload_impl(
    payload: Any,
    key: str | None,
    *,
    policy: str,
    task_values: tuple[str, ...] = (),
) -> Any:
    normalized_key = (key or "").lower()
    if isinstance(payload, str):
        if policy == "stub" and normalized_key in {
            "sha256",
            "task_hash",
            "entities_sha",
            "target_entity_hashes",
            "constraint_hashes",
            "description_sha256",
            "selected_object_id_hash",
            "object_evidence_hash",
            "title_stub",
            "title_hash",
            "container_lineage_hash",
            "list_lineage_hash",
        }:
            return _redacted_private_text(payload)
        if policy == "stub" and normalized_key in PRIVATE_CONTEXT_TEXT_KEYS:
            return _redacted_private_text(payload)
        if (
            policy == "stub"
            and normalized_key
            and normalized_key not in SAFE_CONTEXT_TEXT_KEYS
        ):
            return _redacted_private_text(payload)
        text = _mark_task_matches(payload, task_values) if task_values else payload
        return redact_context_text(text)
    if isinstance(payload, dict):
        return {
            str(k): _sanitize_payload_impl(
                v, str(k), policy=policy, task_values=task_values
            )
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [
            _sanitize_payload_impl(item, key, policy=policy, task_values=task_values)
            for item in payload
        ]
    if isinstance(payload, tuple):
        return [
            _sanitize_payload_impl(item, key, policy=policy, task_values=task_values)
            for item in payload
        ]
    return payload
