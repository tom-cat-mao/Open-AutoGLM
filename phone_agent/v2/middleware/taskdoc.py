"""TaskDoc middleware: pin the task board + a derived flow line into context.

Per ``AGENTS.md`` §2.3 (U3 update). This middleware
is the render hook of the TaskDoc increment: before every model call it
re-injects, as a single ``[TASK_DOC]`` system message kept at the tail of the
transcript, the pinned block:

    [目标 / 路线(如有) / 关键事实(如有)]   (from ``TaskDoc.render``)
    ## 流程线（最近 N 步）                (derived here, U3)

Because the block is re-emitted each turn (old copy removed, fresh copy
appended), it is *pinned* and *compression-immune* — the image-pruning /
compaction passes never strip the model's view of what it is trying to do or of
the trajectory so far.

**Flow line (U3, replaces the old stagnation nudge).** The flow line is derived
*entirely from the transcript* — the ``tool_calls`` on ``AIMessage``s and the
matching ``ToolMessage`` receipts — so it needs no new session state. Each
entry reads ``#N <intent> → <tool><target> → <result>`` where ``<intent>`` is
the ``intent`` argument the model passed under the U3 output contract (missing
intent renders as ``（未声明）``). Only the most recent :data:`MAX_FLOW_ITEMS`
entries are shown. The old ``seen_states``/``nudged`` stagnation machinery is
gone: the running flow line is a stronger, non-directive anti-loop signal than a
one-shot nudge, and it costs no bespoke detection heuristic.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import RemoveMessage, SystemMessage

# Stable prefix for the pinned task-board id so the block is easy to spot in a
# transcript; the suffix is per-injection unique so add_messages re-appends the
# refreshed copy at the tail instead of replacing it in place.
_TASKDOC_ID_PREFIX = "__taskdoc__"

# How many trailing flow-line entries to render (design: 最近 8 条).
MAX_FLOW_ITEMS = 8

_FLOW_HEADER = {
    "cn": "## 流程线（最近 {n} 步）",
    "en": "## Flow (recent {n})",
}
_INTENT_MISSING = {"cn": "（未声明）", "en": "(no intent)"}
_RESULT_OK = {"cn": "ok", "en": "ok"}
_RESULT_PENDING = {"cn": "…", "en": "…"}


def _is_cn(lang: str) -> bool:
    return (lang or "").strip().lower() in {"cn", "zh", "zh-cn", "zh_cn", "chinese"}


def _clip(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _result_text(msg: Any) -> str:
    """Best-effort text of a ToolMessage receipt (str content or text blocks)."""

    if msg is None:
        return ""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content)


def _bracket_label(text: str) -> str | None:
    """Extract the first ``「…」`` label from a receipt (e.g. 已点击「上海」(ax_3))."""

    if not text:
        return None
    start = text.find("「")
    if start == -1:
        return None
    end = text.find("」", start + 1)
    if end == -1:
        return None
    label = text[start + 1 : end].strip()
    return label or None


def _flow_target(name: str, args: dict, result_text: str) -> str:
    """A compact target descriptor for a tool call (``「上海」`` / ``(ax_3)`` / …)."""

    if name in {"tap", "long_press"}:
        desc = (args.get("target_description") or "").strip()
        if desc:
            return f"「{_clip(desc, 16)}」"
        label = _bracket_label(result_text)
        if label:
            return f"「{_clip(label, 16)}」"
        mark = (args.get("target_mark_id") or "").strip()
        return f"({mark})" if mark else ""
    if name == "type_text":
        return f"「{_clip(str(args.get('text', '')), 16)}」"
    if name == "launch_app":
        return f"「{_clip(str(args.get('app_name', '')), 16)}」"
    if name == "scroll":
        direction = str(args.get("direction", "")).strip()
        return f" {direction}" if direction else ""
    if name == "locate":
        return f"「{_clip(str(args.get('description', '')), 16)}」"
    if name == "wait":
        seconds = args.get("seconds")
        return f" {seconds}s" if seconds is not None else ""
    return ""


def _result_status(text: str, lang: str) -> str:
    """Compress a receipt into a flow-line status (``ok`` / short error text)."""

    text = (text or "").strip()
    if not text:
        return _RESULT_PENDING["cn" if _is_cn(lang) else "en"]
    if text.startswith("OK."):
        return _RESULT_OK["cn" if _is_cn(lang) else "en"]
    first = text.splitlines()[0].strip()
    return _clip(first, 48)


def _derive_flow_lines(messages: list, lang: str, max_items: int = MAX_FLOW_ITEMS) -> list[str]:
    """Derive the flow-line entries from the transcript's tool_call/tool_result pairs.

    Reads ``AIMessage.tool_calls`` (name + args, which under the U3 contract carry
    ``intent``/``note``) and pairs each with its ``ToolMessage`` receipt (matched by
    ``tool_call_id``). Returns rendered lines, oldest→newest, capped to the trailing
    ``max_items``. Robust to malformed messages (skips anything without a usable
    tool_call shape) so a broken transcript never crashes the loop.
    """

    cn = _is_cn(lang)
    intent_missing = _INTENT_MISSING["cn" if cn else "en"]

    # First pass: index ToolMessage receipts by the tool_call id they answer.
    results_by_id: dict[str, Any] = {}
    for msg in messages or []:
        cid = getattr(msg, "tool_call_id", None)
        if cid is not None:
            results_by_id[str(cid)] = msg

    entries: list[tuple[str, dict, Any]] = []
    for msg in messages or []:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "?")
            args = call.get("args")
            if not isinstance(args, dict):
                args = {}
            cid = call.get("id")
            result_msg = results_by_id.get(str(cid)) if cid is not None else None
            entries.append((name, args, result_msg))

    if not entries:
        return []

    shown = entries[-max_items:]
    base_index = len(entries) - len(shown) + 1
    lines: list[str] = []
    for offset, (name, args, result_msg) in enumerate(shown):
        step = base_index + offset
        intent = _clip(str(args.get("intent") or ""), 40) or intent_missing
        result_text = _result_text(result_msg)
        target = _flow_target(name, args, result_text)
        status = _result_status(result_text, lang)
        line = f"#{step} {intent} → {name}{target} → {status}"
        note = _clip(str(args.get("note") or ""), 40)
        if note:
            line += f"｜{note}" if cn else f" | {note}"
        lines.append(line)
    return lines


class TaskDocMiddleware(AgentMiddleware):
    """before_model: inject the pinned TaskDoc block + the derived flow line."""

    def __init__(self, session: Any, lang: str = "cn", nudge_steps: int = 5) -> None:
        super().__init__()
        self.session = session
        self.lang = lang
        # ``nudge_steps`` is retained for backward-compatible construction only
        # (U3 removed the stagnation nudge); it is intentionally unused.
        self.nudge_steps = nudge_steps
        # Id of the last injected task-board message (removed + refreshed next turn).
        self._injected_id: str | None = None

    # ------------------------------------------------------------------
    def _flow_block(self, messages: list) -> str:
        """Render the ``## 流程线`` section from the transcript, or ``""`` if empty."""

        lines = _derive_flow_lines(messages, self.lang)
        if not lines:
            return ""
        header = _FLOW_HEADER["cn" if _is_cn(self.lang) else "en"].format(n=len(lines))
        return header + "\n" + "\n".join(lines)

    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        doc = getattr(self.session, "task_doc", None)
        if doc is None:
            return None

        try:
            rendered = doc.render(self.lang)
        except Exception:  # noqa: BLE001 - a broken render must not crash the loop
            return None
        if not rendered:
            # Empty document: nothing pinned (and no trajectory worth pinning yet).
            return None

        messages = state.get("messages", []) if isinstance(state, dict) else []
        block = "[TASK_DOC]\n" + rendered
        flow = self._flow_block(messages)
        if flow:
            block = block + "\n\n" + flow

        new_id = f"{_TASKDOC_ID_PREFIX}{uuid.uuid4().hex}"
        out: list[Any] = []
        if self._injected_id is not None:
            # Drop the previous pinned copy so exactly one block exists, refreshed
            # at the tail (RemoveMessage on the stale id, then append the new one).
            out.append(RemoveMessage(id=self._injected_id))
        out.append(SystemMessage(content=block, id=new_id))
        self._injected_id = new_id
        return {"messages": out}

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)


def build_taskdoc_middleware(
    session: Any, lang: str = "cn", nudge_steps: int = 5
) -> TaskDocMiddleware:
    """Build a :class:`TaskDocMiddleware` bound to ``session``.

    The session is captured directly so the middleware can read
    ``session.task_doc`` before every model call. ``nudge_steps`` is accepted for
    backward-compatible call sites (U3 removed the stagnation nudge) and ignored.
    """

    return TaskDocMiddleware(session, lang=lang, nudge_steps=nudge_steps)


__all__ = [
    "TaskDocMiddleware",
    "build_taskdoc_middleware",
    "MAX_FLOW_ITEMS",
]
