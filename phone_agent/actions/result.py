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
