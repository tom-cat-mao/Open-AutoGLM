"""Shared action execution result type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActionResult:
    """Result of an action execution."""

    success: bool
    should_finish: bool
    message: str | None = None
    requires_confirmation: bool = False
    # F7: add-only machine metadata passthrough (e.g. the Launch tool's
    # resolved package + user term, carried so the reflect step can back a
    # learning record with front-foreground verification). Never user-facing
    # content; tools that do not set it leave it None.
    metadata: dict | None = None
