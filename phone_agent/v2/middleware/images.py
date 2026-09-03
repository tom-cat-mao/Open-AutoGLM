"""Context-pruning middleware: bound image + OBS-text growth before each model call.

Per AGENTS.md (P0 #3): before every model call
this middleware runs two independent, idempotent passes over the transcript:

1. **Image pruning** — keep the image blocks of only the newest ``keep_images``
   image-bearing messages; older image blocks are replaced with a
   ``[screen#<n> 已剪除]`` text placeholder. This bounds token/latency growth
   from screenshots while preserving a textual trail of which screens existed.

2. **OBS marks folding** — keep the full ``marks (K): <digest>`` section of only
   the newest ``keep_marks`` ``[OBS]`` text blocks; older ones collapse to a
   one-line ``[OBS] app=X screen#N [marks 已折叠:K]`` placeholder. Marks-first
   grounding requires the model to address the *latest* observation's mark ids,
   so folding stale digests is safe and reinforces that discipline.

Both passes mutate message content in place and are idempotent: a placeholder
carries no image block and a folded line carries no ``marks (`` marker, so a
re-run leaves already-processed history byte-identical (stable prefix outside
the rolling window). Modified messages are returned once, deduped by identity,
so the ``add_messages`` reducer replaces them in place.

``ImagePruningMiddleware`` / ``build_image_middleware`` remain as compatibility
aliases for the pre-S1 image-only behaviour (keep newest 1).
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware

_OBS_PREFIX = "[OBS] "
_MARKS_MARKER = "\nmarks ("


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


def _is_obs_marks_block(block: Any) -> bool:
    """A text block that is a foldable ``[OBS] ... marks (K): ...`` observation."""

    if not isinstance(block, dict) or block.get("type") != "text":
        return False
    text = block.get("text")
    return isinstance(text, str) and text.startswith(_OBS_PREFIX) and _MARKS_MARKER in text


def _message_has_obs_marks(message: Any) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return any(_is_obs_marks_block(block) for block in content)
    return False


def _image_block_screen_seq(block: Any) -> int | None:
    """Return the ``screen_seq`` carried on an image block, if any.

    Tools stamp the real observation sequence onto the image block
    (``{image_url, screen_seq}``); the placeholder reuses it so the pruned
    marker points back at the true frame instead of a crop-order counter.
    """
    if isinstance(block, dict):
        seq = block.get("screen_seq")
        if isinstance(seq, int):
            return seq
    return None


def _prune_message_images(message: Any, screen_no: int) -> bool:
    """Replace image blocks in *message* with a text placeholder in place.

    ``screen_no`` is the fallback crop-order counter; when the image block
    carries a real ``screen_seq`` that value is used instead so the placeholder
    points back at the true frame.

    Returns ``True`` if the message was modified.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return False
    new_content: list[Any] = []
    changed = False
    for block in content:
        if _is_image_block(block):
            seq = _image_block_screen_seq(block)
            label_no = seq if seq is not None else screen_no
            new_content.append(
                {"type": "text", "text": f"[screen#{label_no} 已剪除]"}
            )
            changed = True
        else:
            new_content.append(block)
    if changed:
        message.content = new_content
    return changed


def _fold_message_marks(message: Any) -> bool:
    """Collapse the ``marks (K): <digest>`` section of OBS text blocks in place.

    Keeps the ``[OBS] app=X screen#N`` header and appends ``[marks 已折叠:K]``.
    Returns ``True`` if the message was modified.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return False
    new_content: list[Any] = []
    changed = False
    for block in content:
        if _is_obs_marks_block(block):
            text = block["text"]
            head, _, rest = text.partition(_MARKS_MARKER)
            count = rest.split(")", 1)[0].strip() if rest else "?"
            new_content.append(
                {"type": "text", "text": f"{head} [marks 已折叠:{count}]"}
            )
            changed = True
        else:
            new_content.append(block)
    if changed:
        message.content = new_content
    return changed


class ContextPruningMiddleware(AgentMiddleware):
    """Bound image + OBS-marks growth before each model call (two idempotent passes)."""

    def __init__(self, keep_images: int = 2, keep_marks: int = 2) -> None:
        super().__init__()
        # Never fully strip context: at least the newest bearer is retained.
        self.keep_images = max(1, int(keep_images))
        self.keep_marks = max(1, int(keep_marks))

    def _prune_images(self, messages: list[Any]) -> list[Any]:
        image_indices = [
            idx for idx, msg in enumerate(messages) if _message_has_image(msg)
        ]
        if len(image_indices) <= self.keep_images:
            return []
        prune_indices = image_indices[: -self.keep_images]
        modified: list[Any] = []
        # Prefer the real screen_seq on each image block; the chronological
        # counter (oldest = screen#1) is only a fallback when it is absent.
        for screen_no, idx in enumerate(prune_indices, start=1):
            if _prune_message_images(messages[idx], screen_no):
                modified.append(messages[idx])
        return modified

    def _fold_old_marks(self, messages: list[Any]) -> list[Any]:
        obs_indices = [
            idx for idx, msg in enumerate(messages) if _message_has_obs_marks(msg)
        ]
        if len(obs_indices) <= self.keep_marks:
            return []
        fold_indices = obs_indices[: -self.keep_marks]
        modified: list[Any] = []
        for idx in fold_indices:
            if _fold_message_marks(messages[idx]):
                modified.append(messages[idx])
        return modified

    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        messages = state.get("messages") or []
        modified: list[Any] = []
        modified.extend(self._prune_images(messages))
        modified.extend(self._fold_old_marks(messages))
        if not modified:
            return None
        # A message hit by both passes appears twice: dedup by identity so the
        # add_messages reducer replaces it once (same id => in-place replace).
        seen: set[int] = set()
        deduped: list[Any] = []
        for msg in modified:
            if id(msg) in seen:
                continue
            seen.add(id(msg))
            deduped.append(msg)
        return {"messages": deduped}

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)


# -- compatibility: pre-S1 image-only pruning (keep newest 1) ------------------
class ImagePruningMiddleware(ContextPruningMiddleware):
    """Legacy alias: image-only pruning that keeps just the newest screenshot."""

    def __init__(self) -> None:
        super().__init__(keep_images=1, keep_marks=1_000_000)


def build_context_pruning_middleware(
    keep_images: int = 2, keep_marks: int = 2
) -> ContextPruningMiddleware:
    return ContextPruningMiddleware(keep_images=keep_images, keep_marks=keep_marks)


def build_image_middleware() -> ImagePruningMiddleware:
    return ImagePruningMiddleware()


__all__ = [
    "ContextPruningMiddleware",
    "build_context_pruning_middleware",
    "ImagePruningMiddleware",
    "build_image_middleware",
]
