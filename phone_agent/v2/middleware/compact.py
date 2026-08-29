"""Two-threshold auto-compact middleware (A4 §3): fold the ancient context.

Where ``middleware/images.py`` does *fine-grained* context hygiene (roll off old
screenshots + fold old marks digests), this middleware does the *coarse* fold: as
the transcript approaches the model's context window it summarises the **ancient
segment** into one structured hand-off block, keeping only a recent verbatim tail
plus the compression-immune anchors (the system prompt + the pinned ``[TASK_DOC]``
block). It sits **before** the pruner in the middleware order so it collapses
whole turns first and the pruner then trims what remains.

Two thresholds, both measured as an estimate of the current context tokens
(``len // 4`` for text + 1500 per image, :mod:`._tokens`) against the inferred
context window:

* **T1 warn** (``compact_warn_ratio``, default 0.75) — inject a single
  ``SystemMessage`` (once per run) telling the model to write anything important
  into the TaskDoc (compaction-immune) and wind down loose exploration. Pure
  information, no context change.
* **T2 force** (``compact_trigger_ratio``, default 0.92) — call a text-only LLM
  (no tools; ``config.memory_model`` falling back to the main model) to produce a
  phone-oriented structured hand-off summary (目标/路线进度/关键事实/动作史/
  错误与修复/用户补充/当前屏幕/下一步), then rebuild the transcript as
  ``[system prompt] + [COMPACT_SUMMARY] + [recent tail] + [fresh-observation hint]
  + [pinned TaskDoc]``. The cut point never splits a ``tool_use``/``tool_result``
  pair and never folds the pinned blocks. Compaction is **iterative**: a prior
  ``[COMPACT_SUMMARY]`` is fed back in as input and superseded. A too-long input
  is retried up to 3 times, dropping the oldest turn-group each time (PTL retry);
  if the summariser still fails it is **fail-open** (skip the fold this turn — the
  pruner + the token budget still bound growth).

After a successful fold a fresh-observation hint asks the model to ``read_screen``
before acting, since the summary replaced the recent screenshots.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from phone_agent.v2.middleware._tokens import (
    estimate_context_tokens,
    estimate_message_tokens,
)

# Pinned-block id prefixes the fold must preserve verbatim (never summarise): the
# TaskDoc board (taskdoc middleware) and this module's own prior summary.
_TASKDOC_ID_PREFIX = "__taskdoc__"
_COMPACT_ID_PREFIX = "__compact__"

# Default context window when the model name carries no size hint (design: 256k).
_DEFAULT_WINDOW = 256_000

# Model-name size hints (checked as lowercase substrings, largest-first so
# "1000k" wins over "100k" etc.).
_WINDOW_HINTS: tuple[tuple[str, int], ...] = (
    ("2m", 2_000_000),
    ("1m", 1_000_000),
    ("1000k", 1_000_000),
    ("512k", 512_000),
    ("256k", 256_000),
    ("200k", 200_000),
    ("128k", 128_000),
    ("100k", 100_000),
    ("64k", 64_000),
    ("32k", 32_000),
    ("16k", 16_000),
    ("8k", 8_000),
)

_WARN_TEXT = {
    "cn": (
        "[COMPACT_WARN] 上下文已接近窗口阈值。请把要紧事实（已选值/价格/关键 id/"
        "下一步计划）用 update_task_doc 写入任务板（压缩免疫），并收尾松散探索、"
        "聚焦关键路线项。"
    ),
    "en": (
        "[COMPACT_WARN] Context is nearing the window threshold. Record key facts "
        "(chosen values / prices / key ids / next steps) into the TaskDoc via "
        "update_task_doc (compaction-immune), and wind down loose exploration to "
        "focus on the critical route items."
    ),
}

_FRESH_OBS_TEXT = {
    "cn": (
        "[COMPACT_DONE] 已将较早的历史压缩为上方交接摘要，近期截图已被折叠。"
        "当前屏幕状态可能与摘要描述不同，请先 read_screen 恢复现场，再继续操作。"
    ),
    "en": (
        "[COMPACT_DONE] Earlier history was compacted into the hand-off summary "
        "above and recent screenshots were folded. The live screen may differ from "
        "the summary — call read_screen to re-observe before acting."
    ),
}

_SUMMARY_SYSTEM = {
    "cn": (
        "你是手机自动化任务的上下文压缩器。把给定历史压缩成结构化交接摘要，"
        "供同一智能体无缝继续执行。只输出摘要本身，不要执行任何操作、不要调用工具、"
        "不要编造未发生的结果。务必保留：已选择的值、价格/金额、关键 mark/id、"
        "已完成与未完成的步骤、遇到的错误与如何修复、用户的额外要求。"
    ),
    "en": (
        "You compress the context of a phone-automation task into a structured "
        "hand-off summary so the same agent can continue seamlessly. Output only the "
        "summary; do not act, call tools, or invent results. Preserve chosen values, "
        "prices/amounts, key marks/ids, done vs pending steps, errors and how they "
        "were fixed, and any extra user requirements."
    ),
}

_SUMMARY_SECTIONS = {
    "cn": (
        "请输出交接摘要，按这些小节组织：\n"
        "## 目标\n## 路线进度\n## 关键事实\n## 动作史(digest)\n"
        "## 错误与修复\n## 用户补充\n## 当前屏幕状态\n## 下一步建议"
    ),
    "en": (
        "Produce the hand-off summary with these sections:\n"
        "## Goal\n## Route progress\n## Key facts\n## Action history (digest)\n"
        "## Errors and fixes\n## User additions\n## Current screen\n## Next steps"
    ),
}


def infer_context_window(model_name: str | None, override: int | None) -> int:
    """Resolve the context window: explicit override > model-name hint > default."""

    if override and int(override) > 0:
        return int(override)
    name = (model_name or "").lower()
    for hint, value in _WINDOW_HINTS:
        if hint in name:
            return value
    return _DEFAULT_WINDOW


def _is_cn(lang: str) -> bool:
    return (lang or "").strip().lower() in {"cn", "zh", "zh-cn", "zh_cn", "chinese"}


def _pinned_id(message: Any) -> bool:
    mid = getattr(message, "id", None) or ""
    return mid.startswith(_TASKDOC_ID_PREFIX) or mid.startswith(_COMPACT_ID_PREFIX)


def _text_of(message: Any) -> str:
    """Flatten a message's content to text; images -> ``[截图]`` placeholder."""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype in {"image_url", "image"} or "image_url" in block:
                    parts.append("[截图]")
            elif isinstance(block, str):
                parts.append(block)
    return " ".join(p for p in parts if p)


def _render_line(message: Any) -> str:
    """Render one conversation message as a single ``role: text`` line."""

    text = _text_of(message).strip()
    if isinstance(message, HumanMessage):
        return f"用户: {text}"
    if isinstance(message, ToolMessage):
        name = getattr(message, "name", None) or "?"
        return f"工具[{name}]: {text}"
    if isinstance(message, AIMessage):
        calls = getattr(message, "tool_calls", None) or []
        call_text = "; ".join(
            f"{c.get('name', '?')}({c.get('args', {})})" for c in calls if isinstance(c, dict)
        )
        head = f"智能体: {text}" if text else "智能体:"
        return f"{head} → 调用 {call_text}" if call_text else head
    if isinstance(message, SystemMessage):
        return f"系统: {text}"
    return f"{type(message).__name__}: {text}"


def _group_turns(conversation: list[Any]) -> list[list[Any]]:
    """Group a conversation slice into turns for PTL truncation.

    A turn starts at a ``HumanMessage`` or an ``AIMessage``; ``ToolMessage``s (and
    stray system messages) attach to the current turn. Used only to drop the
    *oldest* turn on a too-long summary retry.
    """

    groups: list[list[Any]] = []
    for msg in conversation:
        if isinstance(msg, (HumanMessage, AIMessage)) or not groups:
            groups.append([msg])
        else:
            groups[-1].append(msg)
    return groups


def _safe_tail_start(conversation: list[Any], target: int) -> int:
    """Advance ``target`` forward so the tail never starts on a ``ToolMessage``.

    A tail beginning with a ``tool_result`` whose ``tool_use`` was folded into the
    summary would dangle (gateway 400). Advancing forward drops such a leading
    ToolMessage into the summarised (prose) segment, which needs no pairing.
    """

    cut = max(0, min(target, len(conversation)))
    while cut < len(conversation) and isinstance(conversation[cut], ToolMessage):
        cut += 1
    return cut


class CompactMiddleware(AgentMiddleware):
    """before_model: T1 warn + T2 forced structured hand-off compaction."""

    def __init__(
        self,
        session: Any,
        config: Any,
        *,
        model: Any | None = None,
        warn_ratio: float = 0.75,
        trigger_ratio: float = 0.92,
        keep_ratio: float = 0.5,
        max_ptl_retries: int = 3,
        min_fold_messages: int = 4,
        lang: str = "cn",
    ) -> None:
        super().__init__()
        self.session = session
        self.config = config
        self._main_model = model
        self.warn_ratio = _clamp_ratio(warn_ratio, 0.75)
        self.trigger_ratio = _clamp_ratio(trigger_ratio, 0.92)
        # Keep-ratio: how much of the window the recent verbatim tail may occupy
        # after a fold. Kept below warn_ratio so a fold lands comfortably under T1.
        self.keep_ratio = _clamp_ratio(keep_ratio, 0.5)
        if self.keep_ratio >= self.warn_ratio:
            self.keep_ratio = self.warn_ratio * 0.6
        self.max_ptl_retries = max(1, int(max_ptl_retries))
        self.min_fold_messages = max(1, int(min_fold_messages))
        self.lang = lang
        self.window = infer_context_window(
            getattr(config, "model_name", None),
            getattr(config, "context_window", None),
        )
        self._warned = False
        # A lazily built memory model (config.memory_model) reused across folds.
        self._memory_model: Any | None = None
        self._memory_model_built = False

    def reset(self) -> None:
        """Clear per-run state (the one-shot T1 warn guard)."""

        self._warned = False

    # -- thresholds --------------------------------------------------------
    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        messages = state.get("messages") if isinstance(state, dict) else None
        messages = list(messages or [])
        if not messages:
            return None
        total = estimate_context_tokens(messages)

        if total >= self.window * self.trigger_ratio:
            update = self._force_compact(messages)
            if update is not None:
                return update
            # Fold could not run (nothing to fold / summariser failed): fall
            # through so the T1 warn can still fire.

        if not self._warned and total >= self.window * self.warn_ratio:
            self._warned = True
            return {"messages": [SystemMessage(content=self._warn_text())]}
        return None

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)

    # -- T2 forced compaction ---------------------------------------------
    def _force_compact(self, messages: list[Any]) -> dict[str, Any] | None:
        """Build the summary + rebuild the transcript; ``None`` if it can't/won't."""

        head, pinned, conversation, prior_summary = self._partition(messages)

        # Choose the recent tail by a token budget, then snap to a safe boundary.
        cut = self._choose_cut(conversation)
        ancient = conversation[:cut]
        tail = conversation[cut:]
        if len(ancient) < self.min_fold_messages:
            return None  # too little to fold -> skip (avoid a pointless LLM call)

        summary_text = self._summarise(ancient, prior_summary)
        if not summary_text:
            return None  # fail-open: summariser failed -> skip the fold this turn

        summary_msg = SystemMessage(
            content="[COMPACT_SUMMARY]\n" + summary_text, id=_new_compact_id()
        )
        fresh_hint = SystemMessage(content=self._fresh_obs_text())

        rebuilt: list[Any] = [
            *head,
            summary_msg,
            *tail,
            fresh_hint,
            *pinned,
        ]
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *rebuilt]}

    def _partition(
        self, messages: list[Any]
    ) -> tuple[list[Any], list[Any], list[Any], str | None]:
        """Split messages into (head, pinned, conversation, prior_summary_text).

        * ``head``: the leading system prompt(s) (non-pinned SystemMessages at the
          very front) — kept verbatim.
        * ``pinned``: TaskDoc / prior-summary blocks — TaskDoc kept verbatim; the
          prior ``[COMPACT_SUMMARY]`` is pulled out as ``prior_summary`` (fed back
          into the next summary, then superseded).
        * ``conversation``: everything else, in order — the fold candidates.
        """

        head: list[Any] = []
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            if isinstance(msg, SystemMessage) and not _pinned_id(msg):
                head.append(msg)
                idx += 1
            else:
                break

        pinned: list[Any] = []
        conversation: list[Any] = []
        prior_summary: str | None = None
        for msg in messages[idx:]:
            mid = getattr(msg, "id", None) or ""
            if mid.startswith(_COMPACT_ID_PREFIX):
                # Iterative: feed the prior summary text back in, drop the message.
                prior_summary = _strip_marker(_text_of(msg))
                continue
            if mid.startswith(_TASKDOC_ID_PREFIX):
                pinned.append(msg)
                continue
            conversation.append(msg)
        return head, pinned, conversation, prior_summary

    def _choose_cut(self, conversation: list[Any]) -> int:
        """Index into ``conversation``: keep the newest tail under the keep budget."""

        if not conversation:
            return 0
        budget = int(self.window * self.keep_ratio)
        kept = 0
        start = len(conversation)
        for i in range(len(conversation) - 1, -1, -1):
            kept += estimate_context_tokens([conversation[i]])
            if kept > budget:
                break
            start = i
        # Always keep at least the final turn even if a single message is huge.
        start = min(start, len(conversation) - 1)
        return _safe_tail_start(conversation, start)

    # -- summariser --------------------------------------------------------
    def _summarise(self, ancient: list[Any], prior_summary: str | None) -> str | None:
        """Call the text-only summariser with PTL retry; ``None`` on failure."""

        model = self._get_memory_model()
        if model is None:
            return None

        groups = _group_turns(ancient)
        for attempt in range(self.max_ptl_retries):
            # Drop the oldest ``attempt`` turn-groups (PTL truncation).
            usable = groups[attempt:] if attempt < len(groups) else groups[-1:]
            flat = [m for group in usable for m in group]
            messages = self._build_summary_messages(flat, prior_summary)
            try:
                resp = model.invoke(messages)
            except Exception:  # noqa: BLE001 - too-long / flaky -> retry smaller
                continue
            self._record_usage(resp, messages)
            text = _content_text(resp).strip()
            if text:
                return text
        return None

    def _record_usage(self, response: Any, messages: list[Any]) -> None:
        """Best-effort accounting for one successful summariser call."""

        ledger = getattr(self.session, "usage_ledger", None)
        if ledger is None:
            return
        try:
            estimate = estimate_context_tokens(messages) + estimate_message_tokens(
                response
            )
            ledger.record("compact", response, estimate_tokens=estimate)
        except Exception:  # noqa: BLE001 - accounting must never break compaction
            pass

    def _build_summary_messages(
        self, ancient: list[Any], prior_summary: str | None
    ) -> list[Any]:
        cn = _is_cn(self.lang)
        goal_text, route_text, facts_text = self._taskdoc_sections()
        history = "\n".join(_render_line(m) for m in ancient) or "（无历史）"
        prior = prior_summary or ("（无）" if cn else "(none)")
        human = (
            f"【目标】\n{goal_text}\n\n"
            f"【路线进度】\n{route_text}\n\n"
            f"【关键事实】\n{facts_text}\n\n"
            f"【已有摘要】\n{prior}\n\n"
            f"【待压缩的历史动作与观测】\n{history}\n\n"
            f"{_SUMMARY_SECTIONS['cn' if cn else 'en']}"
        )
        return [
            SystemMessage(content=_SUMMARY_SYSTEM["cn" if cn else "en"]),
            HumanMessage(content=human),
        ]

    def _taskdoc_sections(self) -> tuple[str, str, str]:
        """Render the TaskDoc goal / route / facts for the summary prompt."""

        doc = getattr(self.session, "task_doc", None)
        if doc is None:
            return "（无任务板）", "（无路线）", "（无事实）"
        goals: list[str] = []
        base = (getattr(doc, "goal_base", "") or "").strip()
        if base:
            goals.append(base)
        for a in getattr(doc, "amendments", []) or []:
            if str(a).strip():
                goals.append(f"补充：{a}")
        route: list[str] = []
        for it in getattr(doc, "items", []) or []:
            status = getattr(it, "status", "")
            line = f"- [{status}] {getattr(it, 'id', '?')}: {getattr(it, 'content', '')}"
            note = (getattr(it, "evidence_note", None) or "").strip()
            reason = (getattr(it, "reason", None) or "").strip()
            if status == "completed" and note:
                line += f"（证据：{note}）"
            elif status == "blocked" and reason:
                line += f"（原因：{reason}）"
            route.append(line)
        facts = [f"- {f}" for f in (getattr(doc, "facts", []) or [])]
        return (
            "\n".join(goals) or "（无目标）",
            "\n".join(route) or "（无路线）",
            "\n".join(facts) or "（无事实）",
        )

    def _get_memory_model(self) -> Any | None:
        """Lazily resolve the summariser model (memory_model or the main model)."""

        if self._memory_model_built:
            return self._memory_model
        self._memory_model_built = True
        name = getattr(self.config, "memory_model", None)
        if name:
            try:
                from dataclasses import replace

                from phone_agent.v2.model import build_chat_model

                self._memory_model = build_chat_model(replace(self.config, model_name=name))
                return self._memory_model
            except Exception:  # noqa: BLE001 - fall back to the injected main model
                self._memory_model = None
        # Fall back to the main model (no tools bound when invoked directly).
        self._memory_model = self._main_model
        return self._memory_model

    # -- text --------------------------------------------------------------
    def _warn_text(self) -> str:
        return _WARN_TEXT.get(self.lang, _WARN_TEXT["cn"])

    def _fresh_obs_text(self) -> str:
        return _FRESH_OBS_TEXT.get(self.lang, _FRESH_OBS_TEXT["cn"])


def _clamp_ratio(value: float, default: float) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    if ratio <= 0 or ratio > 1:
        return default
    return ratio


def _content_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _strip_marker(text: str) -> str:
    marker = "[COMPACT_SUMMARY]"
    stripped = text.strip()
    if stripped.startswith(marker):
        return stripped[len(marker):].strip()
    return stripped


def _new_compact_id() -> str:
    import uuid

    return f"{_COMPACT_ID_PREFIX}{uuid.uuid4().hex}"


def build_compact_middleware(
    session: Any, config: Any, *, model: Any | None = None
) -> CompactMiddleware:
    """Build a :class:`CompactMiddleware` from resolved config values."""

    return CompactMiddleware(
        session,
        config,
        model=model,
        warn_ratio=getattr(config, "compact_warn_ratio", 0.75),
        trigger_ratio=getattr(config, "compact_trigger_ratio", 0.92),
        lang=getattr(config, "lang", "cn"),
    )


__all__ = [
    "CompactMiddleware",
    "build_compact_middleware",
    "infer_context_window",
]
