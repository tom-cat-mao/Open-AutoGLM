"""Mechanism-level guidance for structured plan failures."""

from __future__ import annotations


def retry_policy_for_layer(layer: str) -> str:
    if layer in {"parse", "adapter"}:
        return "parse_retry"
    if layer == "grounding":
        return "reobserve"
    return "none"


def screenshot_error_fields(code: str, sensitive: bool = False) -> dict:
    return {
        "error_layer": "grounding",
        "error_code": code,
        "recoverable": True,
        "retry_policy": (
            "takeover"
            if sensitive or code == "secure_screenshot_blocked"
            else "reobserve"
        ),
    }


_SUGGESTIONS: dict[tuple[str, str], str] = {
    ("invalid_json", "adapter"): "Re-emit one valid structured JSON action.",
    ("parse_error", "parse"): "Re-emit the action in the configured structured format.",
    ("missing_field", "adapter"): "Re-emit the action with all required fields populated.",
    ("missing_field", "validation"): "Re-emit the action with all required fields populated.",
    ("mark_required", "adapter"): "Use an intent target_mark_id or object selector instead of raw coordinates.",
    ("mark_required", "grounding"): "Reference a current mark or emit locate before a tap-like action.",
    ("unknown_action", "adapter"): "Use a supported canonical action name.",
    ("unknown_action", "validation"): "Use a supported canonical action name.",
    ("unknown_action", "grounding"): "Use a supported canonical action name before grounding.",
    ("unknown_app", "validation"): "Use a known app alias or provide package candidates.",
    ("unsafe_value", "adapter"): "Re-emit only schema-safe fields with allowed primitive values.",
    ("unsafe_value", "validation"): "Re-emit only schema-safe fields with allowed primitive values.",
    ("invalid_metadata", "validation"): "Use do or finish metadata at the canonical action boundary.",
    ("capability_missing", "validation"): "Choose an action with a declared tool capability.",
    ("unsupported_tool_call", "adapter"): "Emit exactly one supported phone action tool call.",
    ("unknown_mark", "grounding"): "Reference a mark from the current screen or emit locate first.",
    ("mark_unavailable", "grounding"): "Reobserve the screen before referencing marks.",
    ("stale_mark", "grounding"): "Reobserve and reference a mark from the current screen.",
    ("stale_screen", "grounding"): "Reobserve before issuing a grounded action.",
    ("hash_mismatch", "grounding"): "Reobserve before issuing a grounded action.",
    ("mark_topology_mismatch", "grounding"): "Reobserve and rebuild mark references.",
    ("low_confidence", "grounding"): "Use locate or reobserve to obtain a confident target mark.",
    ("grounding_ambiguous", "grounding"): "Narrow the target selector before grounding.",
    ("grounding_no_candidate", "grounding"): "Use locate with a bounded scope or reobserve.",
    ("missing_hint", "grounding"): "Provide a mechanism-level target hint for locate.",
    ("mark_generation_failed", "grounding"): "Reobserve and regenerate marks before targeting.",
    ("target_required", "grounding"): "Provide a target mark or object selector.",
    ("bad_bbox", "grounding"): "Reobserve and avoid using malformed mark geometry.",
    ("missing_provider_hash", "grounding"): "Reobserve to bind provider output to the current screen.",
    ("screen_binding_missing", "grounding"): "Reobserve to create a screen binding before grounding.",
    ("screenshot_unavailable", "grounding"): "Reobserve after screenshot capture recovers.",
    ("secure_screenshot_blocked", "grounding"): "Request takeover because the secure screen cannot be inspected.",
    ("adb_screencap_failed", "grounding"): "Reobserve after screenshot capture recovers.",
    ("screenshot_pull_failed", "grounding"): "Reobserve after screenshot capture recovers.",
    ("invalid_screenshot", "grounding"): "Reobserve after screenshot capture recovers.",
}

_LAYER_DEFAULTS: dict[str, str] = {
    "parse": "Re-emit the action in the configured structured format.",
    "adapter": "Re-emit one schema-compliant structured action.",
    "validation": "Re-emit a canonical action that passes validation.",
    "grounding": "Reobserve or use current-screen marks before targeting.",
}


def mechanism_suggestion_for(code: str, layer: str) -> str | None:
    """Return a bounded mechanism-level advisory for a failure code."""

    normalized_code = str(code or "").strip()
    normalized_layer = str(layer or "").strip()
    suggestion = _SUGGESTIONS.get(
        (normalized_code, normalized_layer),
        _LAYER_DEFAULTS.get(normalized_layer),
    )
    if not suggestion:
        return None
    return suggestion[:120]
