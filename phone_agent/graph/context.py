"""Short-term context helpers for graph observability and plan injection."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from phone_agent.config.policy import (
    CONTINUATION_GRANT_STEPS,
    CONTINUATION_MAX_GRANTS,
    CONTINUATION_NOVELTY_NEGATION_STREAK,
    CONTINUATION_WINDOW_STEPS,
    DEFAULT_VERIFICATION_POLICY,
    LOCATE_MAX_PER_RUN,
)

REPEATED_ACTION_THRESHOLD = int(
    DEFAULT_VERIFICATION_POLICY.value("repeated_action_threshold")
)
NOVELTY_EXHAUSTION_STEPS = int(
    DEFAULT_VERIFICATION_POLICY.value("novelty_exhaustion_steps")
)
CONTEXT_MODES = {"off", "observe", "inject"}
DEFAULT_CONTEXT_MODE = "inject"
DEFAULT_CONTEXT_BUDGET: dict[str, int] = {
    # Raised from 400 (P2 milestone latch): the plan agenda now carries the
    # latched "曾观察" markers for every done milestone, and a 400-char cap let
    # those lines get squeezed out by pending-acceptance rows.
    "goal_agenda_chars": 800,
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
    "goal_agenda": 800,
}
DEFAULT_PROMPT_VERSION = "context_harness_v1"
CONTEXT_SECTION_IDS = (
    "goal_agenda",
    "last_action_outcome",
    "failure_memory",
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

    ``failure_code`` and ``locate_count`` give the next plan round's
    ``last_action_outcome`` an explicit failure reason (H3): locate failures
    skip reflect, so the execute-side write is the only channel that carries
    ``grounding_failure_code`` / the attempt count to the plan context.
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
        "failure_code": state.get("grounding_failure_code") or state.get("failure_cause"),
        "locate_count": int(state.get("locate_count") or 0),
        "suggested_strategy": state.get("suggested_strategy"),
    }


def is_failed_outcome(outcome: dict[str, Any]) -> bool:
    """Return whether outcome should enter failure memory."""
    if outcome.get("reflection_verdict") in {"failed", "partial"}:
        return True
    if outcome.get("failure_cause"):
        return True
    return outcome.get("execution_success") is False


FailureMemoryMode = Literal["skip", "verified", "unverified"]


def failure_memory_write_mode(
    *,
    verifier_status: str | None,
    verdict: str | None,
    hard_failure: bool = False,
    disputed: bool = False,
) -> FailureMemoryMode:
    """P3 #2: classify whether a reflect step may write failure memory.

    - ``skip``: disputed (verifier success w/ matched postconditions vs model
      failed, or a wrong_page claim contradicted by observed activity
      migration) and non-failure steps never enter failure memory;
    - ``verified``: consensus failure (hard_failure, or verifier failure + model
      failure) — the deterministic side agrees, memory is trusted;
    - ``unverified``: model-alone failure (verifier unknown / blocked / success
      without matched evidence) — memory is kept, but each item is flagged so
      downstream repeat detection and plan rendering know it was not
      corroborated.
    """

    if verdict not in {"failed", "partial"}:
        return "skip"
    if disputed:
        return "skip"
    if hard_failure or verifier_status == "failure":
        return "verified"
    return "unverified"


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
    *,
    unverified: bool = False,
) -> list[dict[str, Any]]:
    """Append failed outcome and keep the recent bounded window.

    P3 #2: writes are gated by :func:`failure_memory_write_mode` in the reflect
    node — only consensus failures and hard failures are ``verified``; a
    model-alone failure with an unknown verifier is stored with
    ``unverified=True`` so repeated-failure semantics and plan rendering can
    tell corroborated failures apart. Disputed steps never reach this function.
    """

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
    if unverified:
        item["unverified"] = True
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


def action_point(value: Any) -> list[float] | None:
    """Return a rounded ``[x, y]`` point or None when not a 2-tuple of numbers.

    Used for Swipe ``start``/``end`` geometry in tried-action records so the
    repeat guard (P3 #3) can build a swipe repeat key without a target center.
    """

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return [round(float(value[0]), 1), round(float(value[1]), 1)]
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


# ----------------------------------------------------------------------
# F2 earned-continuation credential (pure; written only by node code)
# ----------------------------------------------------------------------

CRITERION_STATUS_RANK = {
    "invalid": 0,
    "contradicted": 0,
    "stale": 0,
    "missing": 0,
    "unobserved": 1,
    "unknown": 1,
    "matched": 2,
}


@dataclass(frozen=True)
class ContinuationCredential:
    """Pure decision about whether a rejected budget window earns another one."""

    granted: bool
    branches: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "branches": list(self.branches),
            "reason": self.reason,
        }


def _ledger_snapshots(
    ledger: list[dict[str, Any]], *, contract_id: str, limit: int
) -> list[dict[str, Any]]:
    """Group ledger entries into per-observation criterion-status snapshots."""

    snapshots: list[dict[str, Any]] = []
    index: dict[tuple[Any, Any], int] = {}
    for item in ledger:
        if not isinstance(item, dict) or item.get("contract_id") != contract_id:
            continue
        key = (item.get("screen_id"), item.get("observation_epoch"))
        if key not in index:
            index[key] = len(snapshots)
            snapshots.append({"per_criterion": {}})
        per_criterion = snapshots[index[key]].get("per_criterion")
        criterion_id = str(item.get("criterion_id") or "")
        if criterion_id:
            per_criterion[criterion_id] = str(item.get("status") or "unknown")
    return snapshots[-limit:]


def _criterion_rank(status: Any) -> int:
    return CRITERION_STATUS_RANK.get(str(status or "unknown"), 0)


def _criterion_moved_up_in_window(
    ledger: list[dict[str, Any]], *, contract_id: str
) -> bool:
    """Branch 1: any criterion's rank rose across the recent window.

    Compares the LATEST snapshot against the EARLIEST snapshot in the window,
    so a pure A-B-A-B oscillation (start rank == end rank) never earns a
    continuation — only a net upward movement does.
    """

    snapshots = _ledger_snapshots(
        ledger, contract_id=contract_id, limit=CONTINUATION_WINDOW_STEPS
    )
    if len(snapshots) < 2:
        return False
    earliest = snapshots[0].get("per_criterion") or {}
    latest = snapshots[-1].get("per_criterion") or {}
    return any(
        _criterion_rank(status) > _criterion_rank(earliest.get(criterion))
        for criterion, status in latest.items()
    )


def _ever_matched_latch(
    ledger: list[dict[str, Any]], *, contract_id: str, criterion_id: str
) -> bool:
    """Fold the ledger into one criterion latch (mirror of ever_matched)."""

    latched = False
    for item in ledger:
        if not isinstance(item, dict):
            continue
        if item.get("contract_id") != contract_id:
            continue
        if str(item.get("criterion_id") or "") != criterion_id:
            continue
        status = str(item.get("status") or "unknown")
        if status == "contradicted":
            latched = False
        elif status == "matched" and item.get("target_app_entered") is True:
            latched = True
    return latched


# Auto-verification kinds are satisfiable by deterministic/恒真 checks (e.g.
# app foreground) regardless of real task progress. H5: continuation branches 2
# and 3 may only count judge-type criteria (vlm_judge / non-auto, incl.
# external_probe) so an always-true auto standard cannot self-grant a window.
AUTO_VERIFICATION_KINDS = frozenset(
    {
        "accessibility_text_match",
        "object_hash_match",
        "object_rank_match",
        "app_or_activity_match",
        "focus_or_keyboard",
        "toggle_state_match",
    }
)


def _criterion_verification_kind(criterion: Any) -> str:
    if isinstance(criterion, dict):
        return str(criterion.get("verification") or "vlm_judge")
    return str(getattr(criterion, "verification", None) or "vlm_judge")


def _criterion_is_judge_kind(verification: str) -> bool:
    """Return whether a contract criterion is judge-type (H5).

    Judge-type = verification is not an auto kind (``vlm_judge`` and
    ``external_probe`` qualify; ``app_or_activity_match`` and the other
    deterministic checks do not).
    """

    return str(verification) not in AUTO_VERIFICATION_KINDS


def _goal_contract_view(state: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """Return (contract_id, [(criterion_name, verification_kind)])."""

    contract = state.get("goal_contract")
    if isinstance(contract, dict):
        contract_id = str(contract.get("runtime_reference") or "")
        rows = [
            (str(item.get("name") or ""), _criterion_verification_kind(item))
            for item in (contract.get("success_criteria") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        return contract_id, rows
    if contract is not None:
        contract_id = str(getattr(contract, "task_hash", "") or "")
        rows = [
            (str(item.name), _criterion_verification_kind(item))
            for item in getattr(contract, "success_criteria", []) or []
            if getattr(item, "name", None)
        ]
        return contract_id, rows
    return "", []


def _latched_criterion_count(state: dict[str, Any]) -> int:
    """Count goal criteria currently pinned by the ever-matched latch.

    H5: only judge-type criteria count toward a continuation latch — an auto
    standard (e.g. app foreground) that matches regardless of task progress
    must not grant a new window on its own.
    """

    contract_id, criteria = _goal_contract_view(state)
    ledger = list(state.get("goal_evidence_ledger") or [])
    return sum(
        1
        for name, verification in criteria
        if name
        and _criterion_is_judge_kind(verification)
        and _ever_matched_latch(
            ledger, contract_id=contract_id, criterion_id=name
        )
    )


def latched_criterion_count(state: dict[str, Any]) -> int:
    """Public alias: count ever-matched latched criteria for window bookkeeping."""

    return _latched_criterion_count(state)


def _judge_near_miss(state: dict[str, Any]) -> bool:
    """Branch 3: the last acceptance's judge named judge-type evidence ≥ 1.

    H5: only matched evidence whose criterion is judge-type in the contract
    counts — auto standards (app_or_activity_match etc.) are excluded, so a
    window cannot be earned by an always-true app-foreground standard.
    """

    evidence = state.get("finish_validation_evidence")
    if not isinstance(evidence, dict):
        return False
    matched = evidence.get("matched") or evidence.get("matched_terminal_evidence")
    if not isinstance(matched, list) or not matched:
        return False
    _, criteria = _goal_contract_view(state)
    judge_names = {
        name for name, verification in criteria if _criterion_is_judge_kind(verification)
    }
    if not judge_names:
        return False
    return any(str(name) in judge_names for name in matched)


def _novelty_streak(state: dict[str, Any]) -> int:
    progress = (state.get("gui_memory") or {}).get("task_progress") or {}
    if not isinstance(progress, dict):
        return 0
    try:
        return int(progress.get("novelty_streak") or 0)
    except (TypeError, ValueError):
        return 0


def continuation_credential(state: dict[str, Any]) -> ContinuationCredential:
    """Decide whether a rejected budget-forced acceptance earns another window.

    Pure function of state; the caller (acceptance node) writes the outcome.
    Branches:
    1. criterion movement — any criterion status rank rose across the last
       ``CONTINUATION_WINDOW_STEPS`` observations (net, so oscillation does not
       grant);
    2. new latch — the ever-matched milestone count grew since the previous
       window boundary (Goal facts; exempt from novelty negation); H5: only
       judge-type criteria count — auto standards (app_or_activity_match etc.)
       are excluded;
    3. judge near-miss — the forced acceptance produced judge-type named
       evidence or at least one hard confirm (H5: auto-standard evidence does
       not count).
    Negation: with no branch 1/3, a novelty streak >= the negation threshold
    (revisiting the same states) denies; branch 2 is never negated.
    """

    contract_id, _ = _goal_contract_view(state)
    ledger = list(state.get("goal_evidence_ledger") or [])

    branches: list[str] = []
    if _criterion_moved_up_in_window(ledger, contract_id=contract_id):
        branches.append("criterion_movement")
    current_latch = _latched_criterion_count(state)
    previous_latch = int(state.get("continuation_last_latch_count") or 0)
    if current_latch > previous_latch:
        branches.append("new_latch")
    if _judge_near_miss(state):
        branches.append("judge_near_miss")

    if branches:
        return ContinuationCredential(
            granted=True,
            branches=tuple(branches),
            reason=";".join(branches),
        )
    if _novelty_streak(state) >= CONTINUATION_NOVELTY_NEGATION_STREAK:
        return ContinuationCredential(
            granted=False,
            branches=(),
            reason="novelty_exhausted",
        )
    return ContinuationCredential(
        granted=False,
        branches=(),
        reason="no_progress_evidence",
    )


def build_budget_section(state: dict[str, Any], lang: str = "cn") -> str:
    """Render the plan-block budget section (F2.2).

    Dynamic context only — never system/goal blocks (P5 prefix-cache). Carries
    the three-piece message: remaining steps in this window, continuations
    granted, and "exhaustion != failure". H2: also carries the per-run locate
    budget so the model sees the remaining locate queries.
    """

    max_steps = int(state.get("max_steps") or 0)
    step_count = int(state.get("step_count") or 0)
    if max_steps <= 0:
        return ""
    remaining = max(0, max_steps - step_count)
    grants = int(state.get("continuation_count") or 0)
    locate_count = int(state.get("locate_count") or 0)
    locate_remaining = max(0, LOCATE_MAX_PER_RUN - locate_count)
    if lang == "en":
        lines = [
            f"Budget: {remaining}/{max_steps} steps left in this window; "
            f"continuations granted {grants}/{CONTINUATION_MAX_GRANTS}."
        ]
        lines.append(f"locate {locate_remaining}/{LOCATE_MAX_PER_RUN} left")
        lines.append(
            "Budget exhaustion is NOT failure — it only triggers a system "
            "acceptance check. If the goal is actually done, finish now and "
            "name the satisfied success criteria; if the task is structurally "
            "infeasible, take_over and explain."
        )
        if remaining <= max(1, max_steps // 4):
            lines.append(
                "This window is nearly exhausted: spend the remaining steps on "
                "the most decisive action."
            )
    else:
        lines = [
            f"预算：本窗口剩余 {remaining}/{max_steps} 步；"
            f"已续命 {grants}/{CONTINUATION_MAX_GRANTS} 次。"
        ]
        lines.append(f"locate 剩余 {locate_remaining}/{LOCATE_MAX_PER_RUN}")
        lines.append(
            "预算耗尽≠失败：它只是触发系统验收；若目标已实际完成请立即 "
            "finish 并点名满足的成功标准；若结构性无法完成请 take_over 说明。"
        )
        if remaining <= max(1, max_steps // 4):
            lines.append("本窗口即将耗尽：请把剩余步骤用在最有决定性的一步上。")
    return " ".join(lines)


def locate_hint_digest(value: Any) -> str | None:
    """Return a privacy-safe short digest of a locate hint (H4).

    The raw hint is regex-redacted first (P0 #10: sanitize at write), then
    sha256-truncated to 8 hex chars, so ``tried_actions`` and repeat keys never
    carry the query text.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    sanitized = sanitize_context_text_regex(value.strip())
    return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:8]


def repeated_action_key(item: dict[str, Any]) -> tuple[Any, ...] | None:
    if not isinstance(item, dict):
        return None
    action = item.get("action")
    surface = item.get("surface")
    if not action or surface is None:
        return None
    if str(action) == "Locate":
        # H4: Locate has no target center; repeat identity comes from the
        # (sanitized, digested) query hint on the same surface.
        return _locate_repeat_key(item, surface)
    center = item.get("target_center")
    if str(action) == "Swipe" and not (
        isinstance(center, (list, tuple)) and len(center) == 2
    ):
        # P3 #3: a Swipe has no target center, so geometry identity comes from
        # its start/end grid plus direction. Before this branch the guard's key
        # was always None for Swipe — the execute repeat guard had a blind spot.
        return _swipe_repeat_key(item, surface)
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        return None
    text_identity = item.get("text_identity")
    if text_identity is None and str(action) in {"Type", "Type_Name"}:
        text_identity = action_text_identity(item.get("text"))
    return (str(action), tuple(center), surface, text_identity)


def _locate_repeat_key(
    item: dict[str, Any], surface: str
) -> tuple[Any, ...] | None:
    digest = item.get("hint_digest")
    if not isinstance(digest, str) or not digest:
        return None
    return (str(item.get("action")), surface, digest)


def _swipe_repeat_key(
    item: dict[str, Any], surface: str
) -> tuple[Any, ...] | None:
    start = action_point(item.get("start"))
    end = action_point(item.get("end"))
    if start is None or end is None:
        return None
    start_grid = (round(start[0] / 50.0), round(start[1] / 50.0))
    end_grid = (round(end[0] / 50.0), round(end[1] / 50.0))
    return (
        str(item.get("action")),
        surface,
        _swipe_direction(item),
        start_grid,
        end_grid,
    )


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
    if isinstance(action, dict) and (
        action.get("_metadata") == "do" or action.get("action") == "Locate"
    ):
        # H4: Locate must enter tried_actions even though it is an internal
        # intent (its _metadata is not guaranteed "do") — the repeat guard's
        # counting source needs the entry or repeated locate queries would
        # never escalate (same treatment as the P3 Swipe fix).
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
                # Swipe geometry (P3 #3): Swipe has no target center, so the repeat
                # guard keys on start/end instead. Both are rounded relative coords.
                "start": action_point(action.get("start")),
                "end": action_point(action.get("end")),
                "surface": state_surface_identity(state),
                "text_identity": action_text_identity(action.get("text")),
                # H4: locate repeat identity (sanitized hint digest), never the
                # raw query text.
                "hint_digest": locate_hint_digest(action.get("target_text_hint")),
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

    Reads raw state fields directly (``action_parsed``,
    ``action_result``, ``failure_memory``,
    ``gui_memory``, ``grounding_observation``) and
    applies :func:`sanitize_context_text_regex` to every string.  No
    key-level stub is applied; stub policy is reserved for the checkpoint
    consumer (``RedactingSerializer``).

    ``summarized_history`` is deliberately **not** injected (P2): it stays in
    state as trace-only memory written by :func:`update_summarized_history`.

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

    current_app = state.get("current_app") or "unknown"

    task_context = state.get("task") if isinstance(state.get("task"), str) else None

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
        # H3: explicit failure code + locate attempt count so a locate failure
        # (which skips reflect) still renders its reason in the next plan block.
        "failure_code": state.get("grounding_failure_code") or state.get("failure_cause"),
        "locate_count": int(state.get("locate_count") or 0),
        "suggested_strategy": plan_safe_strategy,
    }
    raw_advisory = (state.get("action_outcome_summary") or {}).get(
        "verifier_advisory"
    )
    if isinstance(raw_advisory, dict) and raw_advisory:
        outcome["verifier_advisory"] = sanitize_context_payload(
            raw_advisory,
            "verifier_advisory",
            consumer=consumer,
            task_context=task_context,
        )

    failure_memory_budget = max(
        1, int(budget.get("failure_memory_items", 3) or 3)
    )
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
        for item in (state.get("failure_memory") or [])[-failure_memory_budget:]
    ]
    repeated_failure_count = int(state.get("repeated_failure_count") or 0)
    failure_memory_block: dict[str, Any] = {}
    if failure_memory:
        failure_memory_block = {
            "items": failure_memory,
            "repeated_failure_count": repeated_failure_count,
        }

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
    liveness_note = _build_liveness_note(state)

    parts = []
    # Each section is trimmed against its own allowance. Trimming the concatenated
    # block instead starved whichever section was assembled last, which is how loop
    # evidence disappeared exactly as the trajectory started looping.
    budget_section = build_budget_section(state, lang)
    for label, value, section_budget in (
        (
            "goal_agenda",
            goal_agenda,
            budget.get("goal_agenda_chars", _SECTION_BUDGETS["goal_agenda"]),
        ),
        ("budget", budget_section, None),
        ("liveness_note", liveness_note, None),
        ("last_action_outcome", outcome, None),
        ("failure_memory", failure_memory_block, None),
        ("avoid_repeating", avoid_repeating, budget.get("avoid_repeating_chars")),
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

    block, truncated = _trim_plan_block_preserving_agenda(
        title, parts, int(budget["context_block_chars"])
    )
    return block, {
        "context_block_chars": len(block),
        "context_truncated": truncated or component_truncated,
    }


def _trim_plan_block_preserving_agenda(
    title: str, parts: list[str], block_budget: int
) -> tuple[str, bool]:
    """Trim the plan block from the tail, never cutting into the milestone agenda.

    ``goal_agenda`` is the pinned milestone section (P2): the block-level tail
    trim must not drop it, otherwise a large trajectory starves exactly the
    section that says which done subgoals must not be re-done. The agenda is
    the first rendered section, so tail-truncating everything after it is safe;
    only in the pathological case where the agenda alone exceeds the whole
    block budget is the agenda itself trimmed (keeping its head).
    """

    block = title + "\n" + "\n".join(parts)
    if len(block) <= block_budget:
        return block, False
    agenda_index = next(
        (index for index, part in enumerate(parts) if part.startswith("goal_agenda:")),
        None,
    )
    if agenda_index is None:
        return trim_text(block, block_budget)
    head = title + "\n" + "\n".join(parts[: agenda_index + 1])
    if len(head) > block_budget:
        return trim_text(head, block_budget), True
    tail = "\n".join(parts[agenda_index + 1 :])
    remaining = block_budget - len(head) - 1
    if remaining <= 0 or not tail:
        return head, True
    trimmed_tail, tail_truncated = trim_text(tail, remaining)
    return head + "\n" + trimmed_tail, tail_truncated


def _build_liveness_note(state: dict[str, Any]) -> str:
    """Render one natural-language trajectory-liveness sentence for the plan block.

    Liveness no longer routes (model-delegation refactor 2.2): it is telemetry
    plus a hint. The sentence carries the machine state, the novelty streak,
    and the repeat count of the latest target so the model can judge whether
    the trajectory is advancing, exploring, or circling.
    """

    gui_memory = state.get("gui_memory")
    progress = (
        gui_memory.get("task_progress") if isinstance(gui_memory, dict) else {}
    )
    if not isinstance(progress, dict):
        return ""
    liveness = str(progress.get("trajectory_liveness") or "")
    if not liveness:
        return ""
    lang = str(state.get("lang") or "cn")
    novelty = int(progress.get("novelty_streak") or 0)
    stuck_rounds = int(progress.get("stuck_rounds") or 0)
    repeat_count = 0
    tried = gui_memory.get("tried_actions") if isinstance(gui_memory, dict) else []
    if isinstance(tried, list) and tried and isinstance(tried[-1], dict):
        key = repeated_action_key(tried[-1])
        if key is not None:
            repeat_count = sum(
                1
                for item in tried
                if isinstance(item, dict) and repeated_action_key(item) == key
            )
    if lang == "en":
        label = {"advancing": "advancing", "stuck": "stuck", "exploring": "exploring"}.get(
            liveness, liveness
        )
        note = f"Trajectory note: {label}"
        if novelty:
            note += f" (novelty_streak={novelty})"
        if stuck_rounds:
            note += f" (stuck_rounds={stuck_rounds})"
        if repeat_count >= REPEATED_ACTION_THRESHOLD:
            note += f" (latest target repeated {repeat_count}x)"
        note += " -- hint only, judge for yourself."
    else:
        label = {
            "advancing": "推进中",
            "stuck": "陷入循环",
            "exploring": "探索中",
        }.get(liveness, liveness)
        note = f"轨迹提示：当前状态{label}"
        if novelty:
            note += f"（novelty_streak={novelty}）"
        if stuck_rounds:
            note += f"（stuck_rounds={stuck_rounds}）"
        if repeat_count >= REPEATED_ACTION_THRESHOLD:
            note += f"（最近目标已重复 {repeat_count} 次）"
        note += "；仅为提示，请自行判断。"
    return note


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
        if status == "satisfied" and item.get("latched") is True:
            # P2 milestone latch: satisfied at an earlier trusted observation.
            # The marker keeps the milestone visibly pinned across transient
            # current-observation staleness (keyboard popup, partial overlays).
            epoch = item.get("latched_epoch")
            if epoch is not None:
                suffix += (
                    f", observed at epoch {epoch}"
                    if lang == "en"
                    else f", 曾观察于 epoch {epoch}"
                )
            else:
                suffix += (
                    ", observed earlier"
                    if lang == "en"
                    else ", 曾观察"
                )
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
    gui_memory.task_progress + last K=3 action_outcome
    summaries from ``action_ledger`` (trajectory memory, which the current
    single-shot reflect lacks). ``summarized_history`` is excluded (P2):
    it stays a trace-only write target via ``update_summarized_history``.
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
