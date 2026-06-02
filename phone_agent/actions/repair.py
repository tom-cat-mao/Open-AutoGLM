"""Limited action repair.

Repair is deliberately narrow: it may normalize safe aliases/wrappers but must
not invent actions, coordinates, or private text. Repaired output must be sent
back through Validator before safety/execution.
"""

from __future__ import annotations

from typing import Any

from phone_agent.actions.adapter import ACTION_ALIASES


class ActionRepairError(ValueError):
    """Repair error with stable code for trace/eval metadata."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def repair_action(
    action: dict[str, Any],
    *,
    error_code: str | None = None,
    raw_summary: str | None = None,
) -> dict[str, Any]:
    """Return a safely repaired draft action or raise ActionRepairError."""

    repaired = dict(action)
    changed = False

    metadata = repaired.get("_metadata")
    if isinstance(metadata, str):
        normalized_metadata = metadata.strip().lower()
        if normalized_metadata in {"do", "finish"} and normalized_metadata != metadata:
            repaired["_metadata"] = normalized_metadata
            changed = True

    action_name = repaired.get("action")
    if isinstance(action_name, str):
        canonical = ACTION_ALIASES.get(action_name.strip().lower())
        if canonical and canonical != action_name:
            repaired["action"] = canonical
            changed = True

    if not changed:
        detail = f"no safe repair available for {error_code or 'validation_error'}"
        if raw_summary:
            detail = f"{detail}; raw_summary_present=True"
        raise ActionRepairError("repair_not_applicable", detail)
    return repaired
