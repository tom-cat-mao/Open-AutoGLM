"""Shared token estimation for the budget + compaction middleware (A4).

Two consumers need a cheap, dependency-free token gauge:

* :mod:`phone_agent.v2.middleware.budget` — the **cost** budget. It prefers the
  real ``AIMessage.usage_metadata`` (input + output tokens the gateway billed)
  and only falls back to :func:`estimate_context_tokens` when a call reports no
  usage.
* :mod:`phone_agent.v2.middleware.compact` — the **context-size** gauge. It has
  no per-call usage to read (it must decide *before* the call), so it always
  estimates the current transcript against the model's context window.

The estimate is deliberately crude and matches the design note: ``len // 4`` for
text (≈4 chars/token) and a flat :data:`IMAGE_TOKEN_COST` per image block. It is
never exact — it only needs to be monotone and stable so thresholds fire.
"""

from __future__ import annotations

from typing import Any

# Flat per-image token cost. A phone screenshot at gateway tiling lands in the
# ~1-2k token range; 1500 is a middle estimate (design: "len//4 + 图1500").
IMAGE_TOKEN_COST = 1500


def _is_image_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") in {"image_url", "image"}:
        return True
    return "image_url" in block


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens for a plain text string (``len // 4``)."""

    return len(text) // 4 if text else 0


def estimate_message_tokens(message: Any) -> int:
    """Estimate tokens for one message's content (text ``len//4`` + images).

    Accepts either a message object (``.content``) or a raw content value
    (``str`` | ``list[dict]``). Non-text, non-image blocks contribute nothing.
    """

    content = getattr(message, "content", message)
    if isinstance(content, str):
        return estimate_text_tokens(content)
    total = 0
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += estimate_text_tokens(str(block.get("text", "")))
                elif _is_image_block(block):
                    total += IMAGE_TOKEN_COST
            elif isinstance(block, str):
                total += estimate_text_tokens(block)
    return total


def estimate_context_tokens(messages: Any) -> int:
    """Estimate total tokens for a list of messages (sum of per-message)."""

    if not messages:
        return 0
    return sum(estimate_message_tokens(m) for m in messages)


def usage_tokens(message: Any) -> int | None:
    """Return ``input + output`` tokens from ``usage_metadata`` or ``None``.

    ``None`` means the message carries no usable usage (a scripted / cached
    response, or a provider that omits usage) — the caller falls back to an
    estimate. A present-but-zero usage returns ``0`` (a real reported value).
    """

    um = getattr(message, "usage_metadata", None)
    if not isinstance(um, dict):
        return None
    if "input_tokens" not in um and "output_tokens" not in um:
        return None
    try:
        return int(um.get("input_tokens", 0) or 0) + int(um.get("output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None


__all__ = [
    "IMAGE_TOKEN_COST",
    "estimate_text_tokens",
    "estimate_message_tokens",
    "estimate_context_tokens",
    "usage_tokens",
]
