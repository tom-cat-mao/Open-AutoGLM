"""Shared post-action observation capture for Reflect and Acceptance.

Both nodes need the same after-action view of the device: a screenshot, a
built observation, verifier payloads, and optional device signals. Keeping one
implementation here means the two nodes cannot drift into disagreeing about
what "the screen right now" is.
"""

import os
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.graph.observation import build_mark_provider_hints, build_observation
from phone_agent.graph.screenshot_status import (
    screenshot_failure_code,
    screenshot_is_sensitive,
)
from phone_agent.graph.trace import emit_trace
from phone_agent.grounding.factory import build_mark_providers

if TYPE_CHECKING:
    from phone_agent.graph.state import AgentState


def screenshot_failure_update(
    *,
    state: "AgentState",
    config: RunnableConfig,
    screenshot: object,
    current_app: str,
    context_mode: str,
) -> dict:
    """Build a terminal reflect update when a screenshot is unavailable."""

    code = screenshot_failure_code(screenshot) or "screenshot_unavailable"
    sensitive = screenshot_is_sensitive(screenshot)
    failure_cause = (
        "unsafe_or_sensitive"
        if sensitive or code == "secure_screenshot_blocked"
        else "context_lost"
    )
    suggested_strategy = (
        "takeover" if failure_cause == "unsafe_or_sensitive" else "retry"
    )
    error_fields = {
        "error_layer": "grounding",
        "error_code": code,
        "recoverable": True,
        "retry_policy": (
            "takeover" if failure_cause == "unsafe_or_sensitive" else "reobserve"
        ),
    }
    error_message = f"Screenshot unavailable: {code}"
    result_dict = {"success": False, "should_finish": True, "message": error_message}
    emit_trace(
        config,
        state,
        "reflect",
        "reflect_error",
        {
            "message": error_message,
            "failure_cause": failure_cause,
            "grounding_error_code": code,
            **error_fields,
        },
    )
    return {
        "screenshot_b64": None,
        "current_app": current_app,
        "action_result": result_dict,
        "reflection": error_message,
        "action_succeeded": False,
        "reflection_verdict": "failed",
        "failure_cause": failure_cause,
        "suggested_strategy": suggested_strategy,
        "grounding_error": code,
        "grounding_failure_code": code,
        "grounding_provider": "screenshot",
        "observation_retry_count": int(state.get("observation_retry_count") or 0) + 1,
        "finished": True,
        "error": error_message,
        "context_mode": context_mode,
        **error_fields,
    }


def build_after_observation(
    *,
    state: "AgentState",
    config: RunnableConfig,
    screenshot: object,
    current_app: str,
    foreground: object | None,
    observation_epoch: int,
    device_factory: object,
    device_id: str | None,
) -> object:
    """Build a fresh after-action observation for postcondition verification."""

    configurable = config.get("configurable", {})
    screen_marks = configurable.get("after_screen_marks")
    if screen_marks is None:
        screen_marks = configurable.get("screen_marks_after")
    accessibility_enabled = configurable.get("accessibility_marks")
    if accessibility_enabled is None:
        accessibility_enabled = os.getenv(
            "PHONE_AGENT_ACCESSIBILITY_MARKS", ""
        ).lower() in {"1", "true", "yes", "on"}
    provider_name = str(configurable.get("grounding_provider_name") or "").lower()
    hybrid_provider_enabled = provider_name in {
        "hybrid",
        "accessibility_locateanything",
        "uiautomator_locateanything",
    }
    if (
        screen_marks is None
        and accessibility_enabled
        and not hybrid_provider_enabled
        and hasattr(device_factory, "get_screen_marks")
    ):
        try:
            screen_marks = device_factory.get_screen_marks(
                device_id,
                width=getattr(screenshot, "width", 0),
                height=getattr(screenshot, "height", 0),
                timeout=float(configurable.get("accessibility_timeout", 3.0) or 3.0),
                max_marks=int(configurable.get("accessibility_max_marks", 80) or 80),
            )
        except Exception as exc:
            screen_marks = None
            emit_trace(
                config,
                state,
                "reflect",
                "after_accessibility_marks_error",
                {
                    "failure_code": type(exc).__name__,
                    "message": "after accessibility marks unavailable",
                },
            )

    provider_configurable = dict(configurable)
    if not provider_configurable.get("reflect_enable_vlm_grounding"):
        provider_configurable["grounding_provider_name"] = "accessibility"
    if (
        provider_configurable.get("accessibility_tree_dump") is None
        and not provider_configurable.get("skip_accessibility_provider")
        and hasattr(device_factory, "dump_uiautomator_xml")
    ):
        provider_configurable["accessibility_tree_dump"] = lambda timeout=None: (
            device_factory.dump_uiautomator_xml(
                device_id,
                timeout=timeout,
            )
        )
    provider_hints = build_mark_provider_hints(
        task=state.get("task"),
        reflection=state.get("reflection"),
        provider_hints=configurable.get("mark_provider_hints")
        or configurable.get("grounding_hints"),
    )
    return build_observation(
        screenshot=screenshot,
        current_app=current_app,
        marks=screen_marks,
        mark_providers=build_mark_providers(provider_configurable),
        provider_hints=provider_hints,
        provider_timeout=float(configurable.get("grounding_timeout", 10.0) or 10.0),
        foreground=foreground,
        observation_epoch=observation_epoch,
    )


def bounded_observation_summary(
    payload: dict | None, *, task_context: str | None = None
) -> dict:
    """Return prompt/trace-safe screen evidence without raw screenshots or trees."""

    if not isinstance(payload, dict):
        return {}
    safe = sanitize_context_payload(
        payload, consumer="reflect_prompt", task_context=task_context
    )
    if not isinstance(safe, dict):
        return {}
    marks = safe.get("marks")
    if isinstance(marks, list):
        safe["marks"] = marks[:20]
    return safe


def observation_shape_diff(before: dict, after: dict) -> dict:
    """Return a bounded projected mark delta for reflection prompts."""

    before_marks = _marks_by_id(before)
    after_marks = _marks_by_id(after)
    before_ids = set(before_marks)
    after_ids = set(after_marks)
    changed = []
    for mark_id in sorted(before_ids & after_ids):
        before_mark = before_marks[mark_id]
        after_mark = after_marks[mark_id]
        before_shape = {
            "role": before_mark.get("role"),
            "text_summary": before_mark.get("text_summary"),
        }
        after_shape = {
            "role": after_mark.get("role"),
            "text_summary": after_mark.get("text_summary"),
        }
        if before_shape != after_shape:
            changed.append(
                {"mark_id": mark_id, "before": before_shape, "after": after_shape}
            )
    return {
        "before_mark_count": len(before_marks),
        "after_mark_count": len(after_marks),
        "added": [after_marks[key] for key in sorted(after_ids - before_ids)[:20]],
        "removed": [before_marks[key] for key in sorted(before_ids - after_ids)[:20]],
        "changed": changed[:20],
        "unchanged_count": len(before_ids & after_ids) - len(changed),
    }


def _marks_by_id(payload: dict) -> dict[str, dict]:
    marks = payload.get("marks") if isinstance(payload, dict) else None
    iterable = marks.values() if isinstance(marks, dict) else marks or []
    result = {}
    for index, mark in enumerate(iterable):
        if not isinstance(mark, dict):
            continue
        mark_id = str(mark.get("mark_id") or f"mark_{index}")
        result[mark_id] = {
            "mark_id": mark_id,
            "role": mark.get("role"),
            "text_summary": mark.get("text_summary"),
        }
    return result


def state_before_observation_payload(
    state: "AgentState", *, task_context: str | None = None
) -> dict:
    observation = state.get("observation")
    if not isinstance(observation, dict):
        return {}
    snapshot = (
        observation.get("snapshot")
        if isinstance(observation.get("snapshot"), dict)
        else {}
    )
    registry = (
        observation.get("mark_registry")
        if isinstance(observation.get("mark_registry"), dict)
        else {}
    )
    marks_value = registry.get("marks") if isinstance(registry, dict) else []
    if isinstance(marks_value, dict):
        iterable_marks = marks_value.values()
    elif isinstance(marks_value, list):
        iterable_marks = marks_value
    else:
        iterable_marks = []
    marks = []
    for mark in iterable_marks:
        if not isinstance(mark, dict):
            continue
        marks.append(
            {
                "mark_id": mark.get("mark_id"),
                "role": mark.get("role"),
                "text_summary": sanitize_context_payload(
                    mark.get("text_summary") or "",
                    consumer="trace_payload",
                    task_context=task_context,
                ),
            }
        )
    payload = {
        "snapshot": {
            key: snapshot.get(key)
            for key in (
                "screen_id",
                "screen_hash",
                "current_app",
                "foreground_package",
                "foreground_activity",
                "foreground_canonical_id",
                "foreground_known",
                "semantic_screen_id",
                "observation_epoch",
                "mark_set_version",
            )
            if snapshot.get(key) is not None
        },
        "marks": marks,
        "mark_provider_observation": observation.get("mark_provider_observation"),
    }
    return payload


def collect_device_verifier_signals(
    *,
    device_factory: object,
    device_id: str | None,
    config: RunnableConfig,
) -> dict:
    """Collect optional read-only post-action signals for verifier use."""

    configurable = config.get("configurable", {})
    signals: dict[str, object] = {}

    for config_key, output_key in (
        ("focused_editable", "focused_editable"),
        ("focused_window", "focused_window"),
        ("top_activity", "top_activity"),
        ("keyboard_visible", "keyboard_visible"),
    ):
        if config_key in configurable:
            signals[output_key] = configurable[config_key]

    module = getattr(device_factory, "module", None)
    for owner in (device_factory, module):
        if owner is None:
            continue
        if "focused_window" not in signals and hasattr(
            owner, "get_focused_window_or_app"
        ):
            try:
                signals["focused_window"] = owner.get_focused_window_or_app(device_id)
            except Exception:
                pass
        if "top_activity" not in signals and hasattr(owner, "get_top_activity"):
            try:
                signals["top_activity"] = owner.get_top_activity(device_id)
            except Exception:
                pass
        if "keyboard_visible" not in signals and hasattr(owner, "is_keyboard_visible"):
            try:
                signals["keyboard_visible"] = bool(owner.is_keyboard_visible(device_id))
            except Exception:
                pass
    return signals


def verifier_observation_payload(
    observation, *, task_context: str | None = None
) -> dict:
    """Build after-observation text for in-memory verifier matching only."""

    marks = []
    for mark in observation.mark_registry.marks.values():
        row = {
            "mark_id": mark.mark_id,
            "role": mark.role,
            "text_summary": mark.text_summary or "",
        }
        marks.append(row)
    return {
        "snapshot": observation.snapshot.to_dict(),
        "marks": marks,
        "mark_provider_observation": observation.mark_provider_observation,
    }


def sanitize_verifier_observation_payload(
    payload: dict, *, task_context: str | None = None
) -> dict:
    """Return prompt/trace-safe verifier observation without raw UI text."""

    if not isinstance(payload, dict):
        return {}
    safe = sanitize_context_payload(
        payload, consumer="reflect_prompt", task_context=task_context
    )
    if not isinstance(safe, dict):
        return {}
    marks = safe.get("marks")
    if isinstance(marks, list):
        safe["marks"] = marks[:20]
    return safe
