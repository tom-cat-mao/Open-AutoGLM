"""Image-pruning middleware: keep only the newest screenshot in context.

Per refactor-thin-loop-v2 §9.2 (P0 #3, rolling screenshot pruning): before
every model call, every image content block **except those in the newest
image-bearing message** is replaced with a ``[screen#<n> 已剪除]`` text
placeholder. This bounds token/latency growth while preserving a textual trail
of which screenshots existed. The compaction/merge mechanism is deferred to a
later iteration; this is the minimal rolling variant.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware


def _is_image_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if btype in {"image_url", "image"}:
        return True
    # langchain 1.x content-block variants may carry the URL without a strict type.
    return "image_url" in block or "source_type" in block and block.get("type") == "image"


def _message_has_image(message: Any) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return any(_is_image_block(block) for block in content)
    return False


def _prune_message_images(message: Any, screen_no: int) -> bool:
    """Replace image blocks in *message* with a text placeholder in place.

    Returns ``True`` if the message was modified.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return False
    new_content: list[Any] = []
    changed = False
    for block in content:
        if _is_image_block(block):
            new_content.append(
                {"type": "text", "text": f"[screen#{screen_no} 已剪除]"}
            )
            changed = True
        else:
            new_content.append(block)
    if changed:
        message.content = new_content
    return changed


class ImagePruningMiddleware(AgentMiddleware):
    """Prune all but the newest image-bearing message before each model call."""

    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        messages = state.get("messages") or []
        image_indices = [
            idx for idx, msg in enumerate(messages) if _message_has_image(msg)
        ]
        if len(image_indices) <= 1:
            return None

        newest = image_indices[-1]
        modified: list[Any] = []
        # Number pruned screens in chronological order (oldest = screen#1).
        for screen_no, idx in enumerate(image_indices[:-1], start=1):
            message = messages[idx]
            if _prune_message_images(message, screen_no):
                modified.append(message)

        if not modified:
            return None
        # Same message ids => add_messages reducer replaces in place.
        return {"messages": modified}

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)


def build_image_middleware() -> ImagePruningMiddleware:
    return ImagePruningMiddleware()


__all__ = ["ImagePruningMiddleware", "build_image_middleware"]
