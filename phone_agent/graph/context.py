"""Short-term context helpers for graph observability and plan injection."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY

REPEATED_ACTION_THRESHOLD = int(
    DEFAULT_VERIFICATION_POLICY.value("repeated_action_threshold")
)
NOVELTY_EXHAUSTION_STEPS = int(
    DEFAULT_VERIFICATION_POLICY.value("novelty_exhaustion_steps")
)
CONTEXT_MODES = {"off", "observe", "inject"}
DEFAULT_CONTEXT_MODE = "inject"
DEFAULT_CONTEXT_BUDGET: dict[str, int] = {
    "goal_agenda_chars": 400,
    "screen_belief_summary_chars": 300,
    "summarized_history_chars": 800,
    "failure_memory_items": 3,
    "action_outcome_items": 1,
    # Raised from 1500: the per-section allowances below sum to more than that, so
    # some section had to starve on every step regardless of ordering. For scale, the
    # marks block in the same prompt runs ~11k chars, so this is a small share.
    "context_block_chars": 2200,
    "request_recent_messages": 6,
    "reflect_recent_outcomes": 3,
    "reflect_context_block_chars": 1200,
    # Per-section floors. The block used to be concatenated and then cut from the
    # tail, so sections assembled last were starved as `summarized_history` grew with
    # step count — and `tried_actions`, the only record of which target was used, sat
    # near the end. Loop evidence now gets reserved room instead of competing for it.
    "gui_memory_chars": 700,
    "avoid_repeating_chars": 300,
    "tried_action_items": 6,
    "visited_screen_items": 6,
}
_SECTION_BUDGETS = {
    "goal_agenda": 400,
}
DEFAULT_PROMPT_VERSION = "context_harness_v1"
CONTEXT_SECTION_IDS = (
    "goal_agenda",
    "screen_belief",
    "last_action_outcome",
    "failure_memory",
    "summarized_history",
    "short_term_memory",
    "action_ledger",
    "avoid_repeating",
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


def get_context_mode(
    state: dict[str, Any], config: dict[str, Any] | None = None
) -> str:
    """Read context mode from graph config, then state, then default."""
    configurable = config.get("configurable", {}) if config else {}
    return normalize_context_mode(
        configurable.get("context_mode") or state.get("context_mode")
    )


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
      (``{redacted, length}``); safe keys are regex-redacted.  This
      policy is used by ``RedactingSerializer`` at checkpoint egress.

    The legacy ``inject: bool`` parameter is accepted as a backward-compatible
    alias: ``inject=True`` ≡ ``consumer="inject"``, ``inject=False`` ≡
    ``consumer="checkpoint"``.  Passing neither yields the ``default`` policy,
    which is regex-only.
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


def trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim text and return whether truncation happened."""
    if len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 20)] + "...<truncated>", True


def build_screen_belief(
    *,
    current_app: str,
    step_count: int = 0,
    summary: str | None = None,
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
        summary_text, _ = trim_text(
            str(summary), DEFAULT_CONTEXT_BUDGET["screen_belief_summary_chars"]
        )
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
    receipt = state.get("action_receipt") or {}
    raw_message = result.get("message") if isinstance(result, dict) else None
    return {
        "step_count": int(state.get("step_count") or 0),
        "action": action.get("action") if isinstance(action, dict) else None,
        "action_metadata": (
            action.get("_metadata") if isinstance(action, dict) else None
        ),
        "execution_success": (
            result.get("success") if isinstance(result, dict) else None
        ),
        "dispatch_status": (
            receipt.get("dispatch_status") if isinstance(receipt, dict) else None
        ),
        "should_finish": (
            result.get("should_finish") if isinstance(result, dict) else None
        ),
        "result_message_summary": (
            sanitize_context_text_regex(raw_message)
            if isinstance(raw_message, str)
            else raw_message
        ),
        "current_app": state.get("current_app") or "unknown",
        # Which target was acted on. Without it, a history of "action=Tap" lines cannot
        # distinguish progress from re-tapping one element.
        "target_mark_id": (
            (state.get("intent_raw") or {}).get("target_mark_id")
            if isinstance(state.get("intent_raw"), dict)
            else None
        ),
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


def detect_repeated_failure(
    failure_memory: list[dict[str, Any]], outcome: dict[str, Any]
) -> bool:
    """Detect repeated failures by action/cause/app tuple."""
    if not is_failed_outcome(outcome):
        return False
    key = (
        outcome.get("action"),
        outcome.get("failure_cause"),
        outcome.get("current_app"),
    )
    return any(
        (item.get("action"), item.get("failure_cause"), item.get("current_app")) == key
        for item in failure_memory
    )


def update_failure_memory(
    existing: list[dict[str, Any]],
    outcome: dict[str, Any],
    budget: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Append failed outcome and keep the recent bounded window."""
    if not is_failed_outcome(outcome):
        return list(existing or [])
    active_budget = budget or DEFAULT_CONTEXT_BUDGET
    item = {
        "step_count": outcome.get("step_count"),
        "action": outcome.get("action"),
        "current_app": (
            sanitize_context_text_regex(outcome.get("current_app"))
            if isinstance(outcome.get("current_app"), str)
            else outcome.get("current_app")
        ),
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
        f"target={outcome.get('target_mark_id') or 'none'} "
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
        "screen_transition_stream": [],
    }


def action_target_center(
    state: dict[str, Any], action: dict[str, Any]
) -> list[float] | None:
    """Return the grounded tap target centre, rounded to absorb sub-pixel jitter."""

    grounding = state.get("grounding_observation")
    raw = grounding.get("center") if isinstance(grounding, dict) else None
    if not isinstance(raw, (list, tuple)):
        raw = action.get("element")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return [round(float(raw[0]), 1), round(float(raw[1]), 1)]
    except (TypeError, ValueError):
        return None


def state_surface_identity(state: dict[str, Any]) -> str | None:
    """Return the foreground activity of the screen the action was issued on."""

    observation = state.get("observation")
    snapshot = (
        observation.get("snapshot")
        if isinstance(observation, dict) and isinstance(observation.get("snapshot"), dict)
        else {}
    )
    for key in ("foreground_activity", "top_activity", "focused_window"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_context_text_regex(value.strip())
    return None


def detect_repeated_action(
    tried_actions: list[dict[str, Any]], outcome: dict[str, Any]
) -> bool:
    """Detect a target being re-actioned on the same surface, regardless of success.

    :func:`detect_repeated_failure` answers "am I retrying something that failed?" and
    is blind to a loop where every step verifies as successful — repeatedly opening the
    same list item does advance the surface each time, so no per-step check objects.
    That trajectory-level question has no other owner, so it is answered here on the
    identity that stays stable across re-observation: action plus target geometry plus
    surface.
    """

    key = repeated_action_key(outcome)
    if key is None:
        return False
    prior = sum(1 for item in tried_actions or [] if repeated_action_key(item) == key)
    return prior >= REPEATED_ACTION_THRESHOLD


def trajectory_liveness(
    *,
    tried_actions: list[dict],
    visited_states: list[dict],
    criterion_history: list[dict],
    budget: dict[str, int],
) -> dict[str, Any]:
    """Purely classify goal-relative trajectory movement from bounded history."""

    if _criterion_moved_toward_satisfaction(criterion_history):
        return {
            "state": "advancing",
            "reasons": ["criterion_movement"],
            "novelty_streak": 0,
        }

    state_history = [
        item
        for item in (visited_states if visited_states else tried_actions)
        if item.get("_transition_stream")
    ] or (visited_states if visited_states else tried_actions)
    states = [
        (item.get("surface"), item.get("semantic_screen_id") or item.get("screen_id"))
        for item in state_history
        if item.get("surface") is not None
        and (item.get("semantic_screen_id") is not None or item.get("screen_id") is not None)
    ]
    novelty_streak = 0
    seen: set[tuple[Any, Any]] = set()
    for identity in states:
        if identity in seen:
            novelty_streak += 1
        else:
            seen.add(identity)
            novelty_streak = 0
    threshold = max(
        1, int(budget.get("novelty_exhaustion_steps", NOVELTY_EXHAUSTION_STEPS))
    )
    if novelty_streak >= threshold:
        return {
            "state": "stuck",
            "reasons": ["novelty_exhausted"],
            "novelty_streak": novelty_streak,
        }
    return {
        "state": "exploring",
        "reasons": ["new_state" if novelty_streak == 0 else "revisiting_state"],
        "novelty_streak": novelty_streak,
    }


def _criterion_moved_toward_satisfaction(history: list[dict]) -> bool:
    if len(history) < 2:
        return False
    previous = history[-2].get("per_criterion") or {}
    current = history[-1].get("per_criterion") or {}
    rank = {
        "invalid": 0,
        "contradicted": 0,
        "missing": 0,
        "stale": 0,
        "unobserved": 1,
        "unknown": 1,
        "matched": 2,
    }
    return any(
        rank.get(str(status), 0) > rank.get(str(previous.get(criterion)), 0)
        for criterion, status in current.items()
    )


def repeated_action_key(item: dict[str, Any]) -> tuple[Any, ...] | None:
    if not isinstance(item, dict):
        return None
    center = item.get("target_center")
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        return None
    action = item.get("action")
    surface = item.get("surface")
    if not action or surface is None:
        return None
    text_identity = item.get("text_identity")
    if text_identity is None and str(action) in {"Type", "Type_Name"}:
        text_identity = action_text_identity(item.get("text"))
    return (str(action), tuple(center), surface, text_identity)


def action_text_identity(value: Any) -> str | None:
    """Return a privacy-safe identity for text-bearing repeated actions."""

    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def update_gui_memory(
    state: dict[str, Any],
    *,
    current_app: str,
    screen_id: str | None,
    reached_surface: str | None = None,
    semantic_screen_id: str | None = None,
) -> dict[str, Any]:
    """Update GUI memory using only bounded identifiers and sanitized summaries."""

    memory = {**default_gui_memory(), **(state.get("gui_memory") or {})}
    step = int(state.get("step_count") or 0)
    if screen_id:
        visited = list(memory.get("visited_screens") or [])
        item = {
            "screen_id": screen_id,
            "semantic_screen_id": semantic_screen_id,
            "surface": reached_surface,
            "current_app": sanitize_context_text_regex(current_app or "unknown"),
            "step_count": step,
        }
        current_identity = semantic_screen_id or screen_id
        previous_identity = (
            visited[-1].get("semantic_screen_id") or visited[-1].get("screen_id")
            if visited
            else None
        )
        if not visited or (visited[-1].get("surface"), previous_identity) != (
            reached_surface,
            current_identity,
        ):
            visited.append(item)
        memory["visited_screens"] = visited[-10:]
        # Raw transition stream for liveness novelty: visited_screens dedupes
        # adjacent repeats for display, and any path that collapses repeats
        # (same-screen dwell, or a caller-side dedupe) would make "stuck"
        # structurally unreachable. The stream keeps one entry per
        # observation so repeated identities always accumulate.
        stream = list(memory.get("screen_transition_stream") or [])
        stream.append(
            {"surface": reached_surface, "semantic_screen_id": semantic_screen_id, "screen_id": screen_id}
        )
        memory["screen_transition_stream"] = stream[-10:]

    action = state.get("action_parsed") or {}
    if isinstance(action, dict) and action.get("_metadata") == "do":
        tried = list(memory.get("tried_actions") or [])
        raw_failure_cause = state.get("failure_cause")
        tried.append(
            {
                "step_count": step,
                "screen_id": screen_id,
                "action": action.get("action"),
                "mark_id": (
                    (state.get("intent_raw") or {}).get("target_mark_id")
                    if isinstance(state.get("intent_raw"), dict)
                    else None
                ),
                # Geometry and surface, not screen_id, identify a repeated target:
                # screen_id is content-derived, so it changes when a feed reorders
                # while the same card stays in the same place.
                "target_center": action_target_center(state, action),
                "surface": state_surface_identity(state),
                "text_identity": action_text_identity(action.get("text")),
                "result_success": (
                    (state.get("action_result") or {}).get("success")
                    if isinstance(state.get("action_result"), dict)
                    else None
                ),
                "failure_cause": (
                    sanitize_context_text_regex(raw_failure_cause)
                    if isinstance(raw_failure_cause, str)
                    else raw_failure_cause
                ),
            }
        )
        memory["tried_actions"] = tried[-10:]
        if action.get("action") == "Swipe":
            scroll_memory = dict(memory.get("scroll_memory") or {})
            screen_key = screen_id or "unknown"
            scroll_memory[screen_key] = {
                "last_direction": _swipe_direction(action),
                "count": int((scroll_memory.get(screen_key) or {}).get("count") or 0)
                + 1,
            }
            memory["scroll_memory"] = scroll_memory

    progress = dict(memory.get("task_progress") or {})
    if state.get("reflection_verdict"):
        progress["last_verdict"] = state.get("reflection_verdict")
    if state.get("suggested_strategy"):
        raw_strategy = state.get("suggested_strategy")
        progress["suggested_strategy"] = (
            sanitize_context_text_regex(raw_strategy)
            if isinstance(raw_strategy, str)
            else raw_strategy
        )
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
        "current_app": sanitize_context_payload(
            current_app, "current_app", consumer=consumer, task_context=task_context
        ),
        "summary": sanitize_context_payload(
            summary_text, "summary", consumer=consumer, task_context=task_context
        ),
        "loading_or_blocked": bool(screen_belief.get("loading_or_blocked")),
        "unsafe_or_sensitive": bool(screen_belief.get("unsafe_or_sensitive")),
        "confidence": str(screen_belief.get("confidence") or "unknown"),
    }

    action_parsed = state.get("action_parsed") or {}
    action_result = state.get("action_result") or {}
    action_receipt = state.get("action_receipt") or {}
    raw_action = (
        action_parsed.get("action") if isinstance(action_parsed, dict) else None
    )
    raw_message = (
        action_result.get("message") if isinstance(action_result, dict) else None
    )
    # Translate reflect's suggested_strategy into a plan-safe hint. The plan
    # node must not be told "finish" by context — that is the model self-attesting
    # via a prior reflect, which under P0 #13a cannot authorize a finish. Only
    # `continue` / `retry` / `wait` / `takeover` / `go_back` / `swipe_to_find`
    # reach the plan context verbatim; `finish` is downgraded to `continue` so
    # the goal gate, not the context, decides when a finish claim is allowed.
    raw_suggested_strategy = state.get("suggested_strategy")
    plan_safe_strategy = (
        "continue" if raw_suggested_strategy == "finish" else raw_suggested_strategy
    )
    outcome = {
        "step_count": int(state.get("step_count") or 0),
        "action": (
            sanitize_context_payload(
                raw_action, "action", consumer=consumer, task_context=task_context
            )
            if isinstance(raw_action, str)
            else raw_action
        ),
        "execution_success": (
            action_result.get("success") if isinstance(action_result, dict) else None
        ),
        "dispatch_status": (
            action_receipt.get("dispatch_status")
            if isinstance(action_receipt, dict)
            else None
        ),
        "result_message": (
            sanitize_context_payload(
                raw_message, "message", consumer=consumer, task_context=task_context
            )
            if isinstance(raw_message, str)
            else (raw_message or "")
        ),
        "reflection_verdict": state.get("reflection_verdict"),
        "failure_cause": state.get("failure_cause"),
        "suggested_strategy": plan_safe_strategy,
    }

    failure_memory = [
        {
            "step_count": item.get("step_count"),
            "action": item.get("action"),
            "current_app": (
                sanitize_context_payload(
                    item.get("current_app"),
                    "current_app",
                    consumer=consumer,
                    task_context=task_context,
                )
                if isinstance(item.get("current_app"), str)
                else item.get("current_app")
            ),
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
        summarized_history, _ = trim_text(
            summarized_history, budget["summarized_history_chars"]
        )
        component_truncated = True

    goal_agenda = _render_goal_agenda(
        state.get("goal_agenda"), lang=lang, consumer=consumer, task_context=task_context
    )
    gui_memory = _sanitize_gui_memory_for_block(
        state.get("gui_memory"),
        task_context=task_context,
        consumer=consumer,
        budget=budget,
    )
    grounding_obs = sanitize_context_payload(
        state.get("grounding_observation"),
        "grounding_observation",
        consumer=consumer,
    )
    avoid_repeating = _build_avoid_repeating(state)

    parts = []
    # Each section is trimmed against its own allowance. Trimming the concatenated
    # block instead starved whichever section was assembled last, which is how loop
    # evidence disappeared exactly as the trajectory started looping.
    for label, value, section_budget in (
        (
            "goal_agenda",
            goal_agenda,
            budget.get("goal_agenda_chars", _SECTION_BUDGETS["goal_agenda"]),
        ),
        ("screen_belief", belief, None),
        ("last_action_outcome", outcome, None),
        ("latest_failure_memory", failure_memory, None),
        ("avoid_repeating", avoid_repeating, budget.get("avoid_repeating_chars")),
        ("summarized_history", summarized_history, None),
        ("gui_memory", gui_memory, budget.get("gui_memory_chars")),
        ("grounding_observation", grounding_obs, None),
    ):
        if not _context_block_value_is_informative(label, value):
            continue
        rendered = f"{label}: {json.dumps(value, ensure_ascii=False)}"
        if section_budget and len(rendered) > section_budget:
            rendered, section_truncated = trim_text(rendered, section_budget)
            component_truncated = component_truncated or section_truncated
        parts.append(rendered)

    if not parts:
        return "", {"context_block_chars": 0, "context_truncated": False}

    block, truncated = trim_text(
        title + "\n" + "\n".join(parts), budget["context_block_chars"]
    )
    return block, {
        "context_block_chars": len(block),
        "context_truncated": truncated or component_truncated,
    }


def _render_goal_agenda(
    value: Any,
    *,
    lang: str,
    consumer: ContextConsumer,
    task_context: str | None,
) -> str:
    if not isinstance(value, list) or not value:
        return ""
    satisfied: list[str] = []
    unsatisfied: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        description = sanitize_context_payload(
            str(item.get("description") or ""),
            "description",
            consumer=consumer,
            task_context=task_context,
        )
        if not description:
            continue
        predicate = str(item.get("predicate_id") or item.get("verification") or "unknown")
        status = str(item.get("status") or "unknown")
        suffix = predicate
        if item.get("verification") == "vlm_judge" and status != "satisfied":
            suffix += ", pending acceptance" if lang == "en" else ", 待验收"
        rendered = f"{description}({suffix})"
        if status == "satisfied":
            satisfied.append(rendered)
        else:
            unsatisfied.append(rendered)
    lines = []
    if satisfied:
        lines.append(("Satisfied: " if lang == "en" else "已满足: ") + ", ".join(satisfied))
    if unsatisfied:
        lines.append(("Not satisfied: " if lang == "en" else "未满足: ") + ", ".join(unsatisfied))
    return "\n".join(lines)


def _build_avoid_repeating(state: dict[str, Any]) -> dict[str, Any]:
    """Render the loop warning the system prompt already documents.

    ``avoid_repeating`` and ``next_hint`` are described to the model in
    ``config/prompts_zh.py`` / ``prompts_en.py`` but had no writer, so the model was
    told how to react to a signal it could never receive.

    Reads raw ``gui_memory`` rather than the block view: the latter renders entries as
    compact lines, and the repeat key needs the structured fields.
    """

    raw_memory = state.get("gui_memory")
    if not isinstance(raw_memory, dict):
        return {}
    tried = [item for item in (raw_memory.get("tried_actions") or []) if isinstance(item, dict)]
    if not tried:
        return {}
    latest = tried[-1]
    progress = raw_memory.get("task_progress") or {}
    liveness_stuck = (
        isinstance(progress, dict) and progress.get("trajectory_liveness") == "stuck"
    )
    if not (
        liveness_stuck
        or state.get("repeated_action_detected")
        or detect_repeated_action(tried[:-1], latest)
    ):
        return {}
    key = repeated_action_key(latest)
    if key is None:
        return {}
    repeats = sum(1 for item in tried if repeated_action_key(item) == key)
    lang = str(state.get("lang") or "cn")
    return {
        "action": latest.get("action"),
        "mark_id": latest.get("mark_id"),
        "target_center": latest.get("target_center"),
        "surface": _short_surface(latest.get("surface")),
        "repeat_count": repeats,
        "trajectory_liveness": progress.get("trajectory_liveness"),
        "next_hint": (
            "The system will reject another action on this target after the repeat "
            "threshold and consume a step. Choose a different target or strategy."
            if lang == "en"
            else "同一目标超过重复阈值后，系统将拒绝执行并消耗一步；请改换目标或策略。"
        ),
    }


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


def _render_tried_action(
    *,
    step: Any,
    action: Any,
    mark_id: Any,
    center: Any,
    surface: Any,
    success: Any,
    cause: Any,
) -> str:
    """Render one tried action as a compact line.

    A dict per entry spends most of its characters on repeated key names, which is
    what pushed this section past its allowance and cost the newest entries. The line
    form keeps the same fields in roughly a third of the space.
    """

    parts = [f"s{step}", str(action or "?")]
    if mark_id:
        parts.append(str(mark_id))
    if isinstance(center, (list, tuple)) and len(center) == 2:
        parts.append(f"@{center[0]:g},{center[1]:g}")
    if surface:
        parts.append(f"on={surface}")
    parts.append("ok" if success else "fail")
    if cause:
        parts.append(f"cause={cause}")
    return " ".join(parts)


def _short_surface(value: Any) -> str | None:
    """Return the activity half of a ``package/activity`` component.

    The package repeats on every entry and is already carried by ``current_app``, so
    only the activity distinguishes one surface from another.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    activity = value.strip().rsplit("/", 1)[-1]
    return activity.rsplit(".", 1)[-1] or None


def _sanitize_gui_memory_for_block(
    gui_memory: Any,
    *,
    task_context: str | None = None,
    consumer: ContextConsumer = "inject",
    budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Produce a regex-redacted view of gui_memory for context block emission.

    Both lists are bounded here by dropping the OLDEST entries. Character-level
    trimming cuts from the tail, where the newest entries live, so it removed exactly
    the recent history a loop check needs.
    """
    if not isinstance(gui_memory, dict):
        return {}
    active_budget = budget or DEFAULT_CONTEXT_BUDGET
    visited = []
    for item in (gui_memory.get("visited_screens") or [])[
        -active_budget["visited_screen_items"] :
    ]:
        if isinstance(item, dict):
            app = (
                sanitize_context_payload(
                    item.get("current_app"),
                    "current_app",
                    consumer=consumer,
                    task_context=task_context,
                )
                if isinstance(item.get("current_app"), str)
                else item.get("current_app")
            )
            # Same compact line form as tried_actions. The screen_id digest is
            # truncated: it exists only to tell two screens apart, and the full hash
            # spent characters the loop record needs.
            screen_id = str(item.get("screen_id") or "")[:8]
            visited.append(f"s{item.get('step_count')} {app or '?'} {screen_id}".strip())
    tried = []
    for item in (gui_memory.get("tried_actions") or [])[
        -active_budget["tried_action_items"] :
    ]:
        if isinstance(item, dict):
            raw_failure = item.get("failure_cause")
            # Only the fields that identify the target. `screen_id` is omitted: it is
            # content-derived, so it differs across observations of one logical screen
            # and cannot key a repeat. Null fields and the package half of the surface
            # are dropped so entries stay small enough to survive the block budget.
            cause = (
                sanitize_context_payload(
                    raw_failure,
                    "failure_cause",
                    consumer=consumer,
                    task_context=task_context,
                )
                if isinstance(raw_failure, str) and raw_failure
                else None
            )
            tried.append(
                _render_tried_action(
                    step=item.get("step_count"),
                    action=item.get("action"),
                    mark_id=item.get("mark_id"),
                    center=item.get("target_center"),
                    surface=_short_surface(item.get("surface")),
                    success=item.get("result_success"),
                    cause=cause,
                )
            )
    scroll_memory = dict(gui_memory.get("scroll_memory") or {})
    progress = sanitize_context_payload(
        dict(gui_memory.get("task_progress") or {}),
        "task_progress",
        consumer=consumer,
        task_context=task_context,
    )
    # `tried_actions` is emitted first because any residual character-level trimming
    # cuts from the tail. It is the only record of which target was acted on, so it
    # must not be the first thing sacrificed as the trajectory grows.
    return {
        "tried_actions": tried,
        "visited_screens": visited,
        "scroll_memory": scroll_memory,
        "task_progress": progress,
    }


def select_plan_context(
    state: dict[str, Any],
    *,
    mode: str,
    lang: str = "cn",
    prompt_version: str | None = None,
) -> ContextSelectionResult:
    """Select trace-safe context sections without mutating graph state.

    ``observe`` mode selects section IDs for trace metrics but does **not**
    build a context block — observe is meant to be read-only at the LLM
    prompt layer.  ``inject`` mode builds a regex-redacted block via
    :func:`build_plan_context_block` and returns it for prompt injection.
    """
    normalized_mode = normalize_context_mode(mode)
    sections = [
        section for section in CONTEXT_SECTION_IDS if _section_has_value(state, section)
    ]
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


# ----------------------------------------------------------------------
# Reflect context (parallel to plan context, narrower, with trajectory)
# ----------------------------------------------------------------------

REFLECT_CONTEXT_SECTION_IDS = (
    "screen_belief",
    "last_action_outcome",
    "failure_memory",
    "summarized_history",
    "gui_memory.task_progress",
)


def select_reflect_context(
    state: dict[str, Any],
    *,
    mode: str,
    lang: str = "cn",
    prompt_version: str | None = None,
) -> ContextSelectionResult:
    """Select trace-safe context sections for the reflect prompt.

    Mirrors ``select_plan_context`` but with a narrower section set and a
    bounded recent-outcomes trajectory block. ``observe`` mode is read-only;
    ``inject`` builds a regex-redacted block via ``build_reflect_context_block``.
    """
    normalized_mode = normalize_context_mode(mode)
    sections = [
        section
        for section in REFLECT_CONTEXT_SECTION_IDS
        if _section_has_value(state, section)
    ]
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
    block, metrics = build_reflect_context_block(state, lang, consumer="reflect_prompt")
    return ContextSelectionResult(
        context_mode=normalized_mode,
        context_strategy="inject_redacted_block",
        prompt_version=prompt_version or DEFAULT_PROMPT_VERSION,
        selected_sections=sections,
        context_block=block,
        context_block_chars=int(metrics.get("context_block_chars") or 0),
        context_truncated=bool(metrics.get("context_truncated")),
    )


def build_reflect_context_block(
    state: dict[str, Any],
    lang: str = "cn",
    *,
    consumer: ContextConsumer = "reflect_prompt",
) -> tuple[str, dict[str, Any]]:
    """Build a bounded context block for the reflect prompt.

    Includes screen_belief + last_action_outcome + latest failure_memory +
    summarized_history + gui_memory.task_progress + last K=3 action_outcome
    summaries from ``action_ledger`` (trajectory memory, which the current
    single-shot reflect lacks).
    """
    task_context = state.get("task") if isinstance(state.get("task"), str) else None
    budget = state.get("context_budget") or DEFAULT_CONTEXT_BUDGET
    component_truncated = False
    title = (
        "** Reflect Context (belief only; trajectory memory) **"
        if lang == "en"
        else "** 反思上下文（仅为信念与轨迹记忆） **"
    )

    parts: list[str] = []

    # screen_belief
    screen_belief = state.get("screen_belief") or {}
    if isinstance(screen_belief, dict) and _is_informative_belief(
        {
            "summary": screen_belief.get("summary") or "unknown",
            "confidence": screen_belief.get("confidence") or "unknown",
            "loading_or_blocked": bool(screen_belief.get("loading_or_blocked")),
            "unsafe_or_sensitive": bool(screen_belief.get("unsafe_or_sensitive")),
        }
    ):
        belief_summary = sanitize_context_payload(
            screen_belief.get("summary") or "unknown",
            consumer=consumer,
            task_context=task_context,
        )
        parts.append(
            f"screen_belief: {json.dumps({'summary': belief_summary, 'confidence': screen_belief.get('confidence') or 'unknown', 'loading_or_blocked': bool(screen_belief.get('loading_or_blocked')), 'unsafe_or_sensitive': bool(screen_belief.get('unsafe_or_sensitive'))}, ensure_ascii=False)}"
        )

    # last_action_outcome
    outcome = state.get("action_outcome_summary")
    if isinstance(outcome, dict) and _is_informative_outcome(
        {
            "action": outcome.get("action"),
            "execution_success": outcome.get("execution_success"),
            "result_message": outcome.get("result_message_summary"),
            "reflection_verdict": outcome.get("reflection_verdict"),
            "failure_cause": outcome.get("failure_cause"),
            "suggested_strategy": outcome.get("suggested_strategy"),
        }
    ):
        parts.append(
            f"last_action_outcome: {json.dumps(sanitize_context_payload(outcome, consumer=consumer, task_context=task_context), ensure_ascii=False)}"
        )

    # latest_failure_memory
    failure_memory = state.get("failure_memory") or []
    if isinstance(failure_memory, list) and failure_memory:
        last = failure_memory[-1]
        if isinstance(last, dict):
            parts.append(
                f"latest_failure_memory: {json.dumps(sanitize_context_payload(last, consumer=consumer, task_context=task_context), ensure_ascii=False)}"
            )

    # summarized_history
    raw_history = str(state.get("summarized_history") or "")
    if raw_history:
        safe_history = sanitize_context_payload(
            raw_history, consumer=consumer, task_context=task_context
        )
        trimmed_history, hist_truncated = trim_text(
            safe_history, budget["summarized_history_chars"]
        )
        if hist_truncated:
            component_truncated = True
        parts.append(f"summarized_history: {trimmed_history}")

    # gui_memory.task_progress
    gui_memory = state.get("gui_memory") or {}
    if isinstance(gui_memory, dict):
        task_progress = gui_memory.get("task_progress")
        if task_progress:
            parts.append(
                f"task_progress: {json.dumps(sanitize_context_payload(task_progress, consumer=consumer, task_context=task_context), ensure_ascii=False)}"
            )

    # trajectory: last K action_outcome summaries from action_ledger
    k = int(budget.get("reflect_recent_outcomes", 3) or 3)
    action_ledger = state.get("action_ledger") or []
    if isinstance(action_ledger, list) and action_ledger:
        recent = action_ledger[-k:]
        trajectory = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            trajectory.append(
                {
                    "action": item.get("action"),
                    "reflection_verdict": item.get("reflection_verdict"),
                    "failure_cause": item.get("failure_cause"),
                    "suggested_strategy": item.get("suggested_strategy"),
                }
            )
        if trajectory:
            parts.append(
                f"recent_outcomes (last {len(trajectory)}): {json.dumps(trajectory, ensure_ascii=False)}"
            )

    if not parts:
        return "", {"context_block_chars": 0, "context_truncated": False}

    block, block_truncated = trim_text(
        title + "\n" + "\n".join(parts),
        int(budget.get("reflect_context_block_chars", 1200) or 1200),
    )
    return block, {
        "context_block_chars": len(block),
        "context_truncated": block_truncated or component_truncated,
    }


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
        compacted[latest_user_index] = _compact_message(
            messages[latest_user_index], keep_images=True
        )
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
    system_messages = [
        message for message in messages if message.get("role") == "system"
    ][:1]
    tail = messages[-max_recent:]
    bounded: list[dict[str, Any]] = []
    for message in system_messages + tail:
        if message not in bounded:
            bounded.append(message)
    return bounded


def _section_has_value(state: dict[str, Any], section: str) -> bool:
    if section == "goal_agenda":
        return bool(state.get("goal_agenda"))
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
                "execution_success": (
                    result.get("success") if isinstance(result, dict) else None
                ),
                "result_message": (
                    result.get("message") if isinstance(result, dict) else None
                ),
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
            if keep_images
            or not (isinstance(item, dict) and item.get("type") == "image_url")
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
        return sum(
            len(str(key)) + _value_approx_chars(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_value_approx_chars(item) for item in value)
    return len(str(value))


def _approx_tokens(chars: int) -> int:
    return max(0, (int(chars) + 3) // 4)
