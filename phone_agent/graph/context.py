"""Short-term context helpers for graph observability and plan injection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

CONTEXT_MODES = {"off", "observe", "inject"}
DEFAULT_CONTEXT_MODE = "inject"
DEFAULT_CONTEXT_BUDGET: dict[str, int] = {
    "screen_belief_summary_chars": 300,
    "summarized_history_chars": 800,
    "failure_memory_items": 3,
    "action_outcome_items": 1,
    "context_block_chars": 1500,
    "request_recent_messages": 6,
}
DEFAULT_PROMPT_VERSION = "context_harness_v1"
CONTEXT_SECTION_IDS = (
    "screen_belief",
    "last_action_outcome",
    "failure_memory",
    "summarized_history",
    "short_term_memory",
    "action_ledger",
    "gui_memory.visited_screens",
    "gui_memory.tried_actions",
    "gui_memory.scroll_memory",
    "gui_memory.task_progress",
    "grounding_observation",
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
    "goal_not_satisfied",
    "unknown",
}
FAILURE_CAUSE_ALIASES = {
    "app_not_responding_or_loading": "app_not_responding",
    "permission_login_captcha": "permission_or_login_or_captcha",
    "unsafe_or_sensitive_hitl": "unsafe_or_sensitive",
    "coordinate_or_click_offset": "coordinate_or_tap_offset",
    "network": "network_or_loading",
}
# Key-level stub policy is CHECKPOINT-CONSUMER ONLY.
# At state write time, NO stub is applied — only regex inline redaction.
# Stub-by-key fires only when sanitize_context_payload is invoked with
# consumer="checkpoint" (e.g. from the RedactingSerializer at checkpoint egress).
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
# Backward-compat alias: some callers used SAFE_CONTEXT_TEXT_KEYS to ask
# "will this key survive inject=False sanitization?". With the consumer model
# the answer is always "yes at write time; only stubbed at checkpoint egress".
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
    """Replace sensitive patterns inline, preserving surrounding text."""
    if not text:
        return ""
    return SENSITIVE_PATTERN.sub("<redacted>", str(text))


# Canonical name for regex-only sanitization. `redact_context_text` is kept as
# an alias for backward compatibility.
sanitize_context_text_regex = redact_context_text


def _redacted_private_text(text: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
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
    """Map (consumer, inject) to a canonical consumer tag.

    The *inject* bool is a deprecated alias retained for backward compatibility:
    inject=True maps to consumer="inject", inject=False maps to
    consumer="checkpoint". If both are given, *consumer* wins.
    """
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
      every string is regex-redacted (``redact_context_text``); key-level
      classification is ignored.  ``task_context`` may be used only by
      ``inject`` / ``reflect_prompt`` callers for derived fields, replacing
      matching task-sensitive values with ``<matches_task_value>`` before
      regex redaction.
    * ``"checkpoint"``: private-text keys are replaced with a redaction stub
      (``{redacted, length, sha256}``); safe keys are regex-redacted.  This
      policy is used by ``RedactingSerializer`` at checkpoint egress.

    The legacy ``inject: bool`` parameter is accepted as a backward-compatible
    alias: ``inject=True`` ≡ ``consumer="inject"``, ``inject=False`` ≡
    ``consumer="checkpoint"``.  Passing neither yields the ``default`` policy,
    which is regex-only.
    """
    resolved = _resolve_consumer(consumer=consumer, inject=inject)
    policy = CONSUMER_POLICY.get(resolved, CONSUMER_POLICY["default"])
    task_values = _task_sensitive_values(task_context) if resolved in {"inject", "reflect_prompt"} else ()
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
            str(k): _sanitize_payload_impl(v, str(k), policy=policy, task_values=task_values)
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitize_payload_impl(item, key, policy=policy, task_values=task_values) for item in payload]
    if isinstance(payload, tuple):
        return [_sanitize_payload_impl(item, key, policy=policy, task_values=task_values) for item in payload]
    return payload


def trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim text and return whether truncation happened."""
    if len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 20)] + "...<truncated>", True


def build_screen_belief(
    *, current_app: str, step_count: int = 0, summary: str | None = None,
    loading_or_blocked: bool = False,
    unsafe_or_sensitive: bool = False,
    confidence: str = "medium",
) -> dict[str, Any]:
    """Build a short-term screen belief.

    ``summary`` is regex-redacted (``sanitize_context_text_regex``) so that
    phone numbers / emails / API keys echoed from screenshots cannot leak
    through the prompt reflection loop.  No key-level stub is applied — stub
    policy is reserved for the checkpoint consumer
    (``RedactingSerializer``).
    """
    safe_summary: Any = "unknown"
    if summary:
        summary_text, _ = trim_text(str(summary), DEFAULT_CONTEXT_BUDGET["screen_belief_summary_chars"])
        safe_summary = sanitize_context_text_regex(summary_text)
    return {
        "current_app": current_app or "unknown",
        "summary": safe_summary or "unknown",
        "loading_or_blocked": loading_or_blocked,
        "unsafe_or_sensitive": unsafe_or_sensitive,
        "confidence": confidence,
        "updated_step": step_count,
    }


def build_action_outcome_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Build an action outcome summary from graph state.

    Free-text fields (``result_message_summary``) are regex-redacted.  No
    key-level stub is applied at write time; stub policy is reserved for the
    checkpoint consumer (``RedactingSerializer``).
    """
    action = state.get("action_parsed") or {}
    result = state.get("action_result") or {}
    raw_message = result.get("message") if isinstance(result, dict) else None
    return {
        "step_count": int(state.get("step_count") or 0),
        "action": action.get("action") if isinstance(action, dict) else None,
        "action_metadata": action.get("_metadata") if isinstance(action, dict) else None,
        "execution_success": result.get("success") if isinstance(result, dict) else None,
        "should_finish": result.get("should_finish") if isinstance(result, dict) else None,
        "result_message_summary": sanitize_context_text_regex(raw_message)
        if isinstance(raw_message, str) else raw_message,
        "current_app": state.get("current_app") or "unknown",
        "reflection_verdict": state.get("reflection_verdict"),
        "failure_cause": state.get("failure_cause"),
        "suggested_strategy": state.get("suggested_strategy"),
    }


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
    item = {
        "step_count": outcome.get("step_count"),
        "action": outcome.get("action"),
        "current_app": sanitize_context_text_regex(outcome.get("current_app"))
        if isinstance(outcome.get("current_app"), str) else outcome.get("current_app"),
        "failure_cause": outcome.get("failure_cause") or "unknown",
        "suggested_strategy": outcome.get("suggested_strategy"),
    }
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


def default_gui_memory() -> dict[str, Any]:
    """Build bounded GUI short-term memory defaults."""

    return {
        "visited_screens": [],
        "tried_actions": [],
        "scroll_memory": {},
        "task_progress": {},
    }


def update_gui_memory(state: dict[str, Any], *, current_app: str, screen_id: str | None) -> dict[str, Any]:
    """Update GUI memory using only bounded identifiers and sanitized summaries."""

    memory = {**default_gui_memory(), **(state.get("gui_memory") or {})}
    step = int(state.get("step_count") or 0)
    if screen_id:
        visited = list(memory.get("visited_screens") or [])
        item = {
            "screen_id": screen_id,
            "current_app": sanitize_context_text_regex(current_app or "unknown"),
            "step_count": step,
        }
        if not visited or visited[-1].get("screen_id") != screen_id:
            visited.append(item)
        memory["visited_screens"] = visited[-10:]

    action = state.get("action_parsed") or {}
    if isinstance(action, dict) and action.get("_metadata") == "do":
        tried = list(memory.get("tried_actions") or [])
        raw_failure_cause = state.get("failure_cause")
        tried.append(
            {
                "step_count": step,
                "screen_id": screen_id,
                "action": action.get("action"),
                "mark_id": (state.get("intent_raw") or {}).get("target_mark_id")
                if isinstance(state.get("intent_raw"), dict)
                else None,
                "result_success": (state.get("action_result") or {}).get("success")
                if isinstance(state.get("action_result"), dict)
                else None,
                "failure_cause": sanitize_context_text_regex(raw_failure_cause)
                if isinstance(raw_failure_cause, str) else raw_failure_cause,
            }
        )
        memory["tried_actions"] = tried[-10:]
        if action.get("action") == "Swipe":
            scroll_memory = dict(memory.get("scroll_memory") or {})
            screen_key = screen_id or "unknown"
            scroll_memory[screen_key] = {
                "last_direction": _swipe_direction(action),
                "count": int((scroll_memory.get(screen_key) or {}).get("count") or 0) + 1,
            }
            memory["scroll_memory"] = scroll_memory

    progress = dict(memory.get("task_progress") or {})
    if state.get("reflection_verdict"):
        progress["last_verdict"] = state.get("reflection_verdict")
    if state.get("suggested_strategy"):
        raw_strategy = state.get("suggested_strategy")
        progress["suggested_strategy"] = sanitize_context_text_regex(raw_strategy) \
            if isinstance(raw_strategy, str) else raw_strategy
    memory["task_progress"] = progress
    return memory


def _swipe_direction(action: dict[str, Any]) -> str:
    start = action.get("start") or [0, 0]
    end = action.get("end") or [0, 0]
    try:
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
    except Exception:
        return "unknown"
    if abs(dx) > abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def build_plan_context_block(
    state: dict[str, Any],
    lang: str = "cn",
    *,
    consumer: ContextConsumer = "inject",
) -> tuple[str, dict[str, Any]]:
    """Build a bounded, regex-redacted context block for plan injection.

    Reads raw state fields directly (``reflection``, ``action_parsed``,
    ``action_result``, ``screen_belief``, ``failure_memory``,
    ``summarized_history``, ``gui_memory``, ``grounding_observation``) and
    applies :func:`sanitize_context_text_regex` to every string.  No
    key-level stub is applied; stub policy is reserved for the checkpoint
    consumer (``RedactingSerializer``).

    ``consumer`` selects the policy from ``CONSUMER_POLICY`` (today all
    policies resolve to regex-only; the argument is kept so that a future
    "verbose" or "terse" consumer can tune the block without re-introducing
    the inject/observe split).
    """
    budget = state.get("context_budget") or DEFAULT_CONTEXT_BUDGET
    component_truncated = False
    title = "** Short-term Context (belief, not authorization) **"
    if lang != "en":
        title = "** 短期上下文（仅为信念，不代表授权） **"

    reflection = state.get("reflection") or ""
    current_app = state.get("current_app") or "unknown"
    screen_belief = state.get("screen_belief") or {}
    summary_source = reflection or screen_belief.get("summary") or "unknown"
    summary_source_str = str(summary_source)
    if len(summary_source_str) > budget["screen_belief_summary_chars"]:
        component_truncated = True
    summary_text, summary_truncated = trim_text(
        summary_source_str, budget["screen_belief_summary_chars"]
    )
    if summary_truncated:
        component_truncated = True

    task_context = state.get("task") if isinstance(state.get("task"), str) else None
    belief = {
        "current_app": sanitize_context_payload(current_app, "current_app", consumer=consumer, task_context=task_context),
        "summary": sanitize_context_payload(summary_text, "summary", consumer=consumer, task_context=task_context),
        "loading_or_blocked": bool(screen_belief.get("loading_or_blocked")),
        "unsafe_or_sensitive": bool(screen_belief.get("unsafe_or_sensitive")),
        "confidence": str(screen_belief.get("confidence") or "unknown"),
    }

    action_parsed = state.get("action_parsed") or {}
    action_result = state.get("action_result") or {}
    raw_action = action_parsed.get("action") if isinstance(action_parsed, dict) else None
    raw_message = action_result.get("message") if isinstance(action_result, dict) else None
    outcome = {
        "step_count": int(state.get("step_count") or 0),
        "action": sanitize_context_payload(raw_action, "action", consumer=consumer, task_context=task_context)
        if isinstance(raw_action, str) else raw_action,
        "execution_success": action_result.get("success") if isinstance(action_result, dict) else None,
        "result_message": sanitize_context_payload(raw_message, "message", consumer=consumer, task_context=task_context)
        if isinstance(raw_message, str) else (raw_message or ""),
        "reflection_verdict": state.get("reflection_verdict"),
        "failure_cause": state.get("failure_cause"),
        "suggested_strategy": state.get("suggested_strategy"),
    }

    failure_memory = [
        {
            "step_count": item.get("step_count"),
            "action": item.get("action"),
            "current_app": sanitize_context_payload(
                item.get("current_app"), "current_app", consumer=consumer, task_context=task_context
            )
            if isinstance(item.get("current_app"), str) else item.get("current_app"),
            "failure_cause": item.get("failure_cause"),
            "suggested_strategy": item.get("suggested_strategy"),
        }
        for item in (state.get("failure_memory") or [])[-1:]
    ]

    raw_summarized_history = str(state.get("summarized_history") or "")
    if len(raw_summarized_history) > budget["summarized_history_chars"]:
        component_truncated = True
    summarized_history = sanitize_context_payload(
        raw_summarized_history,
        "summarized_history",
        consumer=consumer,
        task_context=task_context,
    )
    if len(summarized_history) > budget["summarized_history_chars"]:
        summarized_history, _ = trim_text(summarized_history, budget["summarized_history_chars"])
        component_truncated = True

    gui_memory = _sanitize_gui_memory_for_block(state.get("gui_memory"), task_context=task_context, consumer=consumer)
    grounding_obs = sanitize_context_payload(
        state.get("grounding_observation"), "grounding_observation", consumer=consumer,
    )

    parts = []
    for label, value in (
        ("screen_belief", belief),
        ("last_action_outcome", outcome),
        ("latest_failure_memory", failure_memory),
        ("summarized_history", summarized_history),
        ("gui_memory", gui_memory),
        ("grounding_observation", grounding_obs),
    ):
        if _context_block_value_is_informative(label, value):
            parts.append(f"{label}: {json.dumps(value, ensure_ascii=False)}")

    if not parts:
        return "", {"context_block_chars": 0, "context_truncated": False}

    block, truncated = trim_text(
        title + "\n" + "\n".join(parts), budget["context_block_chars"]
    )
    return block, {"context_block_chars": len(block), "context_truncated": truncated or component_truncated}


def _context_block_value_is_informative(label: str, value: Any) -> bool:
    if not value:
        return False
    if label == "screen_belief" and isinstance(value, dict):
        return _is_informative_belief(value)
    if label == "last_action_outcome" and isinstance(value, dict):
        return _is_informative_outcome(value)
    if label == "gui_memory" and isinstance(value, dict):
        return _is_informative_gui_memory(value)
    return True


def _is_informative_belief(value: dict[str, Any]) -> bool:
    summary = str(value.get("summary") or "").strip().lower()
    confidence = str(value.get("confidence") or "").strip().lower()
    current_app = str(value.get("current_app") or "").strip().lower()
    return bool(
        value.get("loading_or_blocked")
        or value.get("unsafe_or_sensitive")
        or (summary and summary != "unknown")
        or (confidence and confidence != "unknown")
    )


def _is_informative_outcome(value: dict[str, Any]) -> bool:
    return any(
        value.get(key) not in {None, "", "unknown"}
        for key in (
            "action",
            "execution_success",
            "result_message",
            "reflection_verdict",
            "failure_cause",
            "suggested_strategy",
        )
    )


def _is_informative_gui_memory(value: dict[str, Any]) -> bool:
    return bool(
        value.get("visited_screens")
        or value.get("tried_actions")
        or value.get("scroll_memory")
        or value.get("task_progress")
    )


def _sanitize_gui_memory_for_block(
    gui_memory: Any,
    *,
    task_context: str | None = None,
    consumer: ContextConsumer = "inject",
) -> dict[str, Any]:
    """Produce a regex-redacted view of gui_memory for context block emission."""
    if not isinstance(gui_memory, dict):
        return {}
    visited = []
    for item in (gui_memory.get("visited_screens") or []):
        if isinstance(item, dict):
            visited.append(
                {
                    "screen_id": item.get("screen_id"),
                    "current_app": sanitize_context_payload(
                        item.get("current_app"), "current_app", consumer=consumer, task_context=task_context
                    )
                    if isinstance(item.get("current_app"), str) else item.get("current_app"),
                    "step_count": item.get("step_count"),
                }
            )
    tried = []
    for item in (gui_memory.get("tried_actions") or []):
        if isinstance(item, dict):
            raw_failure = item.get("failure_cause")
            tried.append(
                {
                    "step_count": item.get("step_count"),
                    "screen_id": item.get("screen_id"),
                    "action": item.get("action"),
                    "mark_id": item.get("mark_id"),
                    "result_success": item.get("result_success"),
                    "failure_cause": sanitize_context_payload(
                        raw_failure, "failure_cause", consumer=consumer, task_context=task_context
                    )
                    if isinstance(raw_failure, str) else raw_failure,
                }
            )
    scroll_memory = dict(gui_memory.get("scroll_memory") or {})
    progress = sanitize_context_payload(
        dict(gui_memory.get("task_progress") or {}),
        "task_progress",
        consumer=consumer,
        task_context=task_context,
    )
    return {
        "visited_screens": visited,
        "tried_actions": tried,
        "scroll_memory": scroll_memory,
        "task_progress": progress,
    }


def select_plan_context(
    state: dict[str, Any], *, mode: str, lang: str = "cn", prompt_version: str | None = None
) -> ContextSelectionResult:
    """Select trace-safe context sections without mutating graph state.

    ``observe`` mode selects section IDs for trace metrics but does **not**
    build a context block — observe is meant to be read-only at the LLM
    prompt layer.  ``inject`` mode builds a regex-redacted block via
    :func:`build_plan_context_block` and returns it for prompt injection.
    """
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
    block, metrics = build_plan_context_block(state, lang, consumer="inject")
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
    request. Older text is bounded in the request copy; state messages remain
    untouched for reducer/audit semantics.
    """
    before_chars = _messages_approx_chars(messages)
    compacted = [_compact_message(message, keep_images=False) for message in messages]
    latest_user_index = _latest_user_message_index(messages)
    if latest_user_index is not None:
        compacted[latest_user_index] = _compact_message(messages[latest_user_index], keep_images=True)
    compacted = _bound_request_messages(compacted)
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


def _bound_request_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_recent = DEFAULT_CONTEXT_BUDGET["request_recent_messages"]
    if len(messages) <= max_recent:
        return messages
    system_messages = [message for message in messages if message.get("role") == "system"][:1]
    tail = messages[-max_recent:]
    bounded: list[dict[str, Any]] = []
    for message in system_messages + tail:
        if message not in bounded:
            bounded.append(message)
    return bounded


def _section_has_value(state: dict[str, Any], section: str) -> bool:
    if section == "screen_belief":
        screen_belief = state.get("screen_belief")
        if not isinstance(screen_belief, dict):
            return False
        return _is_informative_belief(
            {
                "summary": screen_belief.get("summary") or "unknown",
                "confidence": screen_belief.get("confidence") or "unknown",
                "loading_or_blocked": bool(screen_belief.get("loading_or_blocked")),
                "unsafe_or_sensitive": bool(screen_belief.get("unsafe_or_sensitive")),
            }
        )
    if section == "last_action_outcome":
        summary = state.get("action_outcome_summary")
        if isinstance(summary, dict) and _is_informative_outcome(
            {
                "action": summary.get("action"),
                "execution_success": summary.get("execution_success"),
                "result_message": summary.get("result_message_summary"),
                "reflection_verdict": summary.get("reflection_verdict"),
                "failure_cause": summary.get("failure_cause"),
                "suggested_strategy": summary.get("suggested_strategy"),
            }
        ):
            return True
        action = state.get("action_parsed") or {}
        result = state.get("action_result") or {}
        return _is_informative_outcome(
            {
                "action": action.get("action") if isinstance(action, dict) else None,
                "execution_success": result.get("success") if isinstance(result, dict) else None,
                "result_message": result.get("message") if isinstance(result, dict) else None,
                "reflection_verdict": state.get("reflection_verdict"),
                "failure_cause": state.get("failure_cause"),
                "suggested_strategy": state.get("suggested_strategy"),
            }
        )
    if section == "failure_memory":
        return bool(state.get("failure_memory"))
    if section == "summarized_history":
        return bool(state.get("summarized_history"))
    if section == "short_term_memory":
        return bool(state.get("short_term_memory"))
    if section == "action_ledger":
        return bool(state.get("action_ledger"))
    if section.startswith("gui_memory."):
        key = section.split(".", 1)[1]
        return bool((state.get("gui_memory") or {}).get(key))
    if section == "grounding_observation":
        return bool(state.get("grounding_observation"))
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
