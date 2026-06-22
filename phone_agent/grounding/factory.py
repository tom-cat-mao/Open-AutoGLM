"""Runtime factory for optional mark providers."""

from __future__ import annotations

import os
from typing import Any

from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.locateanything import DEFAULT_LOCATEANYTHING_MAX_SIZE, LocateAnythingMLXProvider
from phone_agent.grounding.provider import MarkProvider

DEFAULT_GROUNDING_PROVIDER_NAME = "hybrid"


def _resolve_positive_int(value: Any, *, default: int) -> int:
    if value in {None, ""}:
        return default
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _resolve_structure_mode(cfg: dict[str, Any]) -> tuple[str, str | None]:
    explicit = cfg.get("locateanything_structure_mode")
    if explicit not in {None, ""}:
        mode = str(explicit).lower()
        if mode not in {"off", "target", "screen"}:
            raise ValueError("locateanything_structure_mode must be one of: off, target, screen")
        return mode, None
    env_value = os.getenv("PHONE_AGENT_LOCATEANYTHING_STRUCTURE_MODE")
    if env_value in {None, ""}:
        return "off", None
    mode = str(env_value).lower()
    if mode not in {"off", "target", "screen"}:
        return "off", mode
    return mode, None


def build_mark_provider(config: dict[str, Any] | None = None) -> MarkProvider | None:
    """Build a mark provider from runtime config/env without exposing it to tool schemas."""

    cfg = config or {}
    provider = cfg.get("mark_provider") or cfg.get("grounding_provider")
    if provider is not None:
        return provider
    name = str(
        cfg.get("grounding_provider_name")
        or os.getenv("PHONE_AGENT_GROUNDING_PROVIDER", DEFAULT_GROUNDING_PROVIDER_NAME)
    ).lower()
    if name in {"", "none", "disabled", "off"}:
        return None
    if name == "fake":
        return FakeGroundingProvider()
    if name in {"accessibility", "accessibility_tree", "uiautomator"}:
        dump_tree = cfg.get("accessibility_tree_dump") or cfg.get("uiautomator_dump")
        if dump_tree is None:
            return None
        max_marks = _resolve_positive_int(
            cfg.get("accessibility_max_marks") or os.getenv("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS"),
            default=80,
        )
        return AccessibilityTreeProvider(dump_tree=dump_tree, max_marks=max_marks)
    if name in {"locateanything", "locateanything_mlx", "mlx"}:
        return _build_locateanything_provider(cfg)
    return None


def build_mark_providers(config: dict[str, Any] | None = None) -> list[MarkProvider]:
    cfg = config or {}
    providers = cfg.get("mark_providers") or cfg.get("grounding_providers")
    if providers:
        return [provider for provider in providers if provider is not None]
    provider = cfg.get("mark_provider") or cfg.get("grounding_provider")
    if provider is not None:
        return [provider]
    name = str(
        cfg.get("grounding_provider_name")
        or os.getenv("PHONE_AGENT_GROUNDING_PROVIDER", DEFAULT_GROUNDING_PROVIDER_NAME)
    ).lower()
    if name in {"hybrid", "accessibility_locateanything", "uiautomator_locateanything"}:
        built: list[MarkProvider] = []
        dump_tree = cfg.get("accessibility_tree_dump") or cfg.get("uiautomator_dump")
        if dump_tree is not None and not cfg.get("skip_accessibility_provider"):
            built.append(
                AccessibilityTreeProvider(
                    dump_tree=dump_tree,
                    max_marks=_resolve_positive_int(
                        cfg.get("accessibility_max_marks") or os.getenv("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS"),
                        default=80,
                    ),
                )
            )
        built.append(_build_locateanything_provider(cfg))
        return [FallbackMarkProvider(built)]
    provider = build_mark_provider(cfg)
    return [provider] if provider is not None else []


def _build_locateanything_provider(cfg: dict[str, Any]) -> LocateAnythingMLXProvider:
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
    context_max_chars = _resolve_positive_int(
        cfg.get("locateanything_context_max_chars") or os.getenv("PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS"),
        default=0,
    )
    structure_mode, invalid_structure_mode = _resolve_structure_mode(cfg)
    return LocateAnythingMLXProvider(
        model_path=model_path,
        max_size=max_size,
        context_max_chars=context_max_chars,
        structure_mode=structure_mode,
        max_visual_candidates=_resolve_positive_int(
            cfg.get("locateanything_max_visual_candidates")
            or os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_VISUAL_CANDIDATES"),
            default=30,
        ),
        visual_category_budget=_resolve_positive_int(
            cfg.get("locateanything_visual_category_budget")
            or os.getenv("PHONE_AGENT_LOCATEANYTHING_VISUAL_CATEGORY_BUDGET"),
            default=5,
        ),
        max_structure_calls=_resolve_positive_int(
            cfg.get("locateanything_max_structure_calls")
            or os.getenv("PHONE_AGENT_LOCATEANYTHING_MAX_STRUCTURE_CALLS"),
            default=5,
        ),
        invalid_structure_mode=invalid_structure_mode,
    )
