"""Runtime factory for optional grounding providers."""

from __future__ import annotations

import os
from typing import Any

from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.grounding.provider import GroundingProvider


def build_grounding_provider(config: dict[str, Any] | None = None) -> GroundingProvider | None:
    """Build provider from runtime config/env without exposing it to tool schemas."""

    cfg = config or {}
    provider = cfg.get("grounding_provider")
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
        return LocateAnythingMLXProvider(model_path=model_path)
    return None

