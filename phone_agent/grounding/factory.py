"""Runtime factory for optional mark providers."""

from __future__ import annotations

import os
from typing import Any

from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.locateanything import DEFAULT_LOCATEANYTHING_MAX_SIZE, LocateAnythingMLXProvider
from phone_agent.grounding.provider import MarkProvider


def _resolve_positive_int(value: Any, *, default: int) -> int:
    if value in {None, ""}:
        return default
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def build_mark_provider(config: dict[str, Any] | None = None) -> MarkProvider | None:
    """Build a mark provider from runtime config/env without exposing it to tool schemas."""

    cfg = config or {}
    provider = cfg.get("mark_provider") or cfg.get("grounding_provider")
    if provider is not None:
        return provider
    name = str(cfg.get("grounding_provider_name") or os.getenv("PHONE_AGENT_GROUNDING_PROVIDER", "")).lower()
    if name in {"", "none", "disabled", "off"}:
        return None
    if name == "fake":
        return FakeGroundingProvider()
    if name in {"locateanything", "locateanything_mlx", "mlx"}:
        model_path = cfg.get("grounding_model_path") or os.getenv(
            "PHONE_AGENT_LOCATEANYTHING_MODEL", "models/LocateAnything-3B-4bit"
        )
        max_size = _resolve_positive_int(
            cfg.get("locateanything_max_size")
            or cfg.get("grounding_max_size")
            or os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_SIZE")
            or os.getenv("PHONE_AGENT_GROUNDING_MAX_SIZE"),
            default=DEFAULT_LOCATEANYTHING_MAX_SIZE,
        )
        return LocateAnythingMLXProvider(model_path=model_path, max_size=max_size)
    return None


def build_mark_providers(config: dict[str, Any] | None = None) -> list[MarkProvider]:
    provider = build_mark_provider(config)
    return [provider] if provider is not None else []
