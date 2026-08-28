"""Diagnostic evidence middleware: opt-in, full-text (bounded) run evidence.

This is the diagnosis-grade counterpart to :mod:`phone_agent.v2.middleware.trace`.
Where ``TraceMiddleware`` is the P0 #6 compliance artifact (every text value
truncated to 64 chars, base64 never logged), this middleware records the *full*
redacted picture a live-diagnosis run needs — the final context the model
actually saw, the task board, and each tool's raw return — while keeping the two
guarantees that must never bend: sensitive substrings are redacted and image
``base64`` is never written.

Design (``outputs/design-council/ROUND2-D1.md`` §1):

* A **separate** middleware, not a mode switch on ``TraceMiddleware`` — the P0
  contract stays a single, un-flippable branch.
* Shares the base64-drop / sensitive-redaction logic via
  :mod:`phone_agent.v2.middleware._redact`; differs only by (a) no 64-char cap
  and (b) a ``DIAG_MAX_TEXT`` volume bound.
* **Default OFF, zero-cost when off** (``V2Config.diagnostic_evidence``). Enabled
  only by the live-diagnosis skill.
* Mounted **last** in the middleware list so ``before_model`` observes the
  post-image-prune + post-TaskDoc context, and ``wrap_tool_call`` is innermost
  (the raw tool return, before any other middleware reshapes it).

Emits one JSONL line per event to ``<evidence_dir>/<run_id>.evidence.jsonl``.
``hitl_decision`` events are written by the driver layer (the skill's logging
HITL handler), not here — a HITL interrupt unwinds the graph, so
``wrap_tool_call`` never sees the human verdict. ``result_class`` (§2 taxonomy)
is likewise computed at analysis time, not written here.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from phone_agent.v2.middleware._redact import (
    estimate_image_bytes,
    redact_text,
    redact_value_no_base64,
)

# Volume bound for full-text fields (aligns with run_diagnosis' trim()=4000).
DIAG_MAX_TEXT = 4000

# Open-item statuses (inlined so a taskdoc import failure never blocks evidence).
_OPEN_STATUSES = ("pending", "in_progress")

_TASKDOC_MARKER = "[TASK_DOC]"
_PRUNED_MARKER = "已剪除"
_OBS_MARKER = "[OBS]"


def _bounded_text(text: str) -> Any:
    """Redact ``text`` (no 64-char cap) and bound its volume at ``DIAG_MAX_TEXT``.

    Returns the redacted string when within the bound, else a truncation marker
    ``{_truncated, _orig_len, text}`` so downstream analysis keeps both the head
    of the text and the original length. Never returns base64.
    """

    redacted = redact_text(text)
    if len(redacted) <= DIAG_MAX_TEXT:
        return redacted
    return {
        "_truncated": True,
        "_orig_len": len(redacted),
        "text": redacted[:DIAG_MAX_TEXT],
    }


def _iter_text_blocks(content: Any):
    """Yield each text fragment from a str or multimodal ``list[dict]`` content."""

    if isinstance(content, str):
        yield content
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    yield block["text"]
            elif isinstance(block, str):
                yield block


def _is_image_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    return block.get("type") in {"image_url", "image"} or "image_url" in block


def _split_multimodal(content: Any) -> tuple[str, dict[str, Any]]:
    """Split a tool return into (joined text, image summary).

    Multimodal content (``[text block + image block]``, produced once the visual
    reflow lands) is *split*: text blocks are joined into ``result_text``; image
    blocks are reduced to ``{present, screen_seq, bytes}`` — the base64 payload
    is never carried. A plain string return yields no image.
    """

    text = "\n".join(_iter_text_blocks(content)).strip()
    image: dict[str, Any] = {"present": False, "screen_seq": None, "bytes": 0}
    if isinstance(content, list):
        total_bytes = 0
        screen_seq: Any = None
        present = False
        for block in content:
            if not _is_image_block(block):
                continue
            present = True
            payload = block.get("image_url")
            url = ""
            if isinstance(payload, dict):
                url = str(payload.get("url", ""))
            elif isinstance(payload, str):
                url = payload
            total_bytes += estimate_image_bytes(url)
            if screen_seq is None:
                screen_seq = block.get("screen_seq")
        if present:
            image = {"present": True, "screen_seq": screen_seq, "bytes": total_bytes}
    return text, image


def _parse_obs_block(text: str) -> dict[str, Any] | None:
    """Parse a ``[OBS] app=<app> screen#<n>\\nmarks (<c>): ...`` block.

    Returns ``{current_app, screen_seq, mark_count}`` or ``None`` when the text
    carries no OBS header. Kept inline (production must not import the skill).
    """

    if not text or _OBS_MARKER not in text:
        return None
    idx = text.find(_OBS_MARKER)
    segment = text[idx:]
    current_app: str | None = None
    screen_seq: int | None = None
    mark_count: int | None = None

    app_key = "app="
    a = segment.find(app_key)
    if a != -1:
        rest = segment[a + len(app_key) :]
        current_app = rest.split()[0] if rest.split() else None

    seq_key = "screen#"
    s = segment.find(seq_key)
    if s != -1:
        digits = ""
        for ch in segment[s + len(seq_key) :]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            screen_seq = int(digits)

    mk = "marks ("
    m = segment.find(mk)
    if m != -1:
        digits = ""
        for ch in segment[m + len(mk) :]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            mark_count = int(digits)

    return {
        "current_app": current_app,
        "screen_seq": screen_seq,
        "mark_count": mark_count,
    }


def _message_text(message: Any) -> str:
    """Concatenate a message's textual content (str or multimodal blocks)."""

    return "".join(_iter_text_blocks(getattr(message, "content", "")))


def _message_has_image(message: Any) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return any(_is_image_block(block) for block in content)
    return False


class DiagnosticEvidenceMiddleware(AgentMiddleware):
    """Append full-text (bounded), redacted run evidence to a JSONL stream."""

    def __init__(
        self,
        run_id: str,
        evidence_dir: str = "outputs/live-diagnosis/.evidence",
        session: Any | None = None,
        enabled: bool = False,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.evidence_dir = evidence_dir
        self.session = session
        self.enabled = enabled
        self._step = 0
        self._started = False
        self._path: str | None = None
        # doc-change dedupe + stagnation mirror.
        self._last_doc_hash: str | None = None
        self._max_seen = 0
        self._stagnant = 0
        self._last_nudged = False
        if self.enabled:
            os.makedirs(self.evidence_dir, exist_ok=True)
            self._path = os.path.join(self.evidence_dir, f"{run_id}.evidence.jsonl")

    @property
    def evidence_path(self) -> str | None:
        return self._path

    # -- io ----------------------------------------------------------------
    def _write(self, event: dict[str, Any]) -> None:
        if not self.enabled or not self._path:
            return
        event.setdefault("ts", time.time())
        try:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - observability must never crash the loop
            pass

    # -- run_start ---------------------------------------------------------
    def _config_digest(self) -> dict[str, Any]:
        cfg = getattr(self.session, "config", None)
        return {
            "model_name": getattr(cfg, "model_name", None),
            "grounding_provider": getattr(cfg, "grounding_provider", None),
            "max_model_calls": getattr(cfg, "max_model_calls", None),
            "lang": getattr(cfg, "lang", None),
            "taskdoc_enabled": bool(getattr(cfg, "taskdoc_enabled", False)),
        }

    def _emit_run_start(self) -> None:
        if self._started:
            return
        self._started = True
        goal_base = ""
        doc = getattr(self.session, "task_doc", None)
        if doc is not None:
            goal_base = getattr(doc, "goal_base", "") or ""
        self._write(
            {
                "event": "run_start",
                "run_id": self.run_id,
                "task_goal_base": redact_text(goal_base),
                "config_digest": self._config_digest(),
            }
        )

    def before_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        if not self.enabled:
            return None
        try:
            self._emit_run_start()
        except Exception:  # noqa: BLE001
            pass
        return None

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_agent(state, runtime)

    # -- before_model: model_request + taskdoc_snapshot + stagnation -------
    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        if not self.enabled:
            return None
        try:
            # run_start is normally emitted by before_agent; guard here too so a
            # harness that resumes past before_agent still records the header.
            self._emit_run_start()
            self._step += 1
            messages = state.get("messages") if isinstance(state, dict) else None
            messages = messages or []
            self._emit_model_request(messages)
            self._emit_taskdoc_snapshot()
            self._emit_stagnation_if_nudged()
        except Exception:  # noqa: BLE001 - observability must never crash the loop
            pass
        return None

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)

    def _emit_model_request(self, messages: list[Any]) -> None:
        image_messages = 0
        pruned = 0
        taskdoc_present = False
        context_chars = 0
        for msg in messages:
            if _message_has_image(msg):
                image_messages += 1
            text = _message_text(msg)
            if _PRUNED_MARKER in text:
                pruned += text.count(_PRUNED_MARKER)
            if _TASKDOC_MARKER in text:
                taskdoc_present = True
            if text:
                context_chars += len(redact_text(text))
        self._write(
            {
                "event": "model_request",
                "step": self._step,
                "message_count": len(messages),
                "image_message_count": image_messages,
                "pruned_screen_count": pruned,
                "taskdoc_present": taskdoc_present,
                "taskdoc_open_items": self._open_item_count(),
                "context_chars": context_chars,
            }
        )

    def _open_item_count(self) -> int:
        doc = getattr(self.session, "task_doc", None)
        if doc is None:
            return 0
        try:
            return sum(
                1 for it in getattr(doc, "items", []) if it.status in _OPEN_STATUSES
            )
        except Exception:  # noqa: BLE001
            return 0

    def _doc_hash(self, doc: Any) -> str:
        items = getattr(doc, "items", []) or []
        parts = [
            getattr(doc, "goal_base", "") or "",
            "|".join(getattr(doc, "amendments", []) or []),
            "|".join(
                f"{it.id}:{it.content}:{it.status}:{it.reason}" for it in items
            ),
            "|".join(getattr(doc, "facts", []) or []),
        ]
        return "␟".join(parts)

    def _emit_taskdoc_snapshot(self) -> None:
        doc = getattr(self.session, "task_doc", None)
        if doc is None:
            return
        digest = self._doc_hash(doc)
        if digest == self._last_doc_hash:
            return
        self._last_doc_hash = digest
        items = []
        open_count = 0
        for it in getattr(doc, "items", []) or []:
            if it.status in _OPEN_STATUSES:
                open_count += 1
            items.append(
                {
                    "id": it.id,
                    "content": redact_text(it.content or ""),
                    "status": it.status,
                    "reason": redact_text(it.reason) if it.reason else None,
                }
            )
        self._write(
            {
                "event": "taskdoc_snapshot",
                "step": self._step,
                "goal_base": redact_text(getattr(doc, "goal_base", "") or ""),
                "amendments": [redact_text(a) for a in getattr(doc, "amendments", []) or []],
                "items": items,
                "facts": [redact_text(f) for f in getattr(doc, "facts", []) or []],
                "open_item_count": open_count,
            }
        )

    def _emit_stagnation_if_nudged(self) -> None:
        # Mirror the TaskDoc middleware's stagnation counter so the recorded
        # stagnant_steps matches; TaskDoc runs before us and may have already
        # flipped session.nudged this turn.
        seen = getattr(self.session, "seen_states", None)
        count = len(seen) if seen is not None else 0
        if count > self._max_seen:
            self._max_seen = count
            self._stagnant = 0
        else:
            self._stagnant += 1
        nudged = bool(getattr(self.session, "nudged", False))
        if nudged and not self._last_nudged:
            self._write(
                {
                    "event": "stagnation_nudge",
                    "step": self._step,
                    "stagnant_steps": self._stagnant,
                }
            )
        self._last_nudged = nudged

    # -- wrap_tool_call: tool_invoke + tool_observation --------------------
    def _emit_tool_invoke(self, name: str, args: Any) -> None:
        self._write(
            {
                "event": "tool_invoke",
                "step": self._step,
                "tool": name,
                "args": redact_value_no_base64(args, redact_text),
            }
        )

    def _emit_tool_observation(
        self, name: str, content: Any, latency_ms: int, error: str | None
    ) -> None:
        text, image = _split_multimodal(content) if content is not None else ("", {"present": False, "screen_seq": None, "bytes": 0})
        obs = _parse_obs_block(text)
        self._write(
            {
                "event": "tool_observation",
                "step": self._step,
                "tool": name,
                "latency_ms": latency_ms,
                "result_text": _bounded_text(text) if text else "",
                "obs": obs,
                "image": image,
                "error": redact_text(error) if error else None,
            }
        )

    def wrap_tool_call(self, request, handler):  # noqa: ANN001
        if not self.enabled:
            return handler(request)
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        try:
            self._emit_tool_invoke(name, args)
        except Exception:  # noqa: BLE001
            pass
        started = time.perf_counter()
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001 - record then re-raise
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:
                self._emit_tool_observation(
                    name, None, latency_ms, f"{type(exc).__name__}: {exc}"
                )
            except Exception:  # noqa: BLE001
                pass
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            content = getattr(result, "content", None)
            self._emit_tool_observation(name, content, latency_ms, None)
        except Exception:  # noqa: BLE001
            pass
        return result

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001
        if not self.enabled:
            return await handler(request)
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        try:
            self._emit_tool_invoke(name, args)
        except Exception:  # noqa: BLE001
            pass
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:
                self._emit_tool_observation(
                    name, None, latency_ms, f"{type(exc).__name__}: {exc}"
                )
            except Exception:  # noqa: BLE001
                pass
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            content = getattr(result, "content", None)
            self._emit_tool_observation(name, content, latency_ms, None)
        except Exception:  # noqa: BLE001
            pass
        return result

    # -- run_end -----------------------------------------------------------
    def after_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        if not self.enabled:
            return None
        try:
            session = self.session
            terminal = {
                "finished": bool(getattr(session, "finished", False)),
                "takeover_reason": redact_text(getattr(session, "takeover_reason", None) or "")
                or None,
                "finish_summary": redact_text(getattr(session, "finish_summary", None) or "")
                or None,
            }
            self._write(
                {"event": "run_end", "steps": self._step, "terminal": terminal}
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.after_agent(state, runtime)


def build_diagnostic_middleware(
    run_id: str,
    evidence_dir: str = "outputs/live-diagnosis/.evidence",
    session: Any | None = None,
    enabled: bool = False,
) -> DiagnosticEvidenceMiddleware:
    """Build a :class:`DiagnosticEvidenceMiddleware` bound to ``session``."""

    return DiagnosticEvidenceMiddleware(
        run_id, evidence_dir=evidence_dir, session=session, enabled=enabled
    )


__all__ = [
    "DiagnosticEvidenceMiddleware",
    "build_diagnostic_middleware",
    "DIAG_MAX_TEXT",
]
