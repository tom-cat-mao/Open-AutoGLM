"""JSONL trace middleware: redacted, per-run observability.

Per refactor-thin-loop-v2 §9.3 (P0 #6, egress redaction): every model call and
tool call is appended as a JSONL event to ``<trace_dir>/<run_id>.jsonl``.

Redaction rules (applied to every logged text value):
  * text values longer than 64 chars are truncated (``…`` suffix, original
    length recorded);
  * sensitive substrings (phone/email/order/captcha/api-key/JWT/…) are replaced
    via :func:`phone_agent.config.redact.redact_context_text`;
  * screenshot ``base64`` is never logged — only ``screen_seq`` and byte length.

Events: ``model_call`` / ``tool_call`` / ``tool_result`` / ``run_end`` with a
timestamp, step index, tool name, redacted args, latency (ms), and error text.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from phone_agent.config.redact import redact_context_text

_MAX_TEXT_CHARS = 64


def _redact_text(value: str) -> str:
    redacted = redact_context_text(value)
    if len(redacted) > _MAX_TEXT_CHARS:
        return redacted[:_MAX_TEXT_CHARS] + "…"
    return redacted


def _redact_value(value: Any) -> Any:
    """Recursively redact a JSON-able value; drop image base64 payloads."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        btype = value.get("type")
        if btype in {"image_url", "image"} or "image_url" in value:
            payload = value.get("image_url")
            url = ""
            if isinstance(payload, dict):
                url = str(payload.get("url", ""))
            elif isinstance(payload, str):
                url = payload
            return {
                "type": "image",
                "screen_seq": value.get("screen_seq"),
                "bytes": _estimate_image_bytes(url),
            }
        return {str(k): _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _estimate_image_bytes(url: str) -> int:
    """Estimate raw byte length of a data: URL without logging its content."""
    if not url:
        return 0
    marker = "base64,"
    idx = url.find(marker)
    b64 = url[idx + len(marker) :] if idx != -1 else url
    # base64 encodes 3 bytes per 4 chars.
    return (len(b64) * 3) // 4


def redact_args(args: Any) -> Any:
    """Public helper: redact a tool-call args mapping for trace logging."""
    return _redact_value(args)


class TraceMiddleware(AgentMiddleware):
    """Append redacted model/tool events to a per-run JSONL trace file."""

    def __init__(self, run_id: str, trace_dir: str = ".traces", enabled: bool = True) -> None:
        super().__init__()
        self.run_id = run_id
        self.trace_dir = trace_dir
        self.enabled = enabled
        self._step = 0
        self._path: str | None = None
        if self.enabled:
            os.makedirs(self.trace_dir, exist_ok=True)
            self._path = os.path.join(self.trace_dir, f"{run_id}.jsonl")

    @property
    def trace_path(self) -> str | None:
        return self._path

    def _write(self, event: dict[str, Any]) -> None:
        if not self.enabled or not self._path:
            return
        event.setdefault("ts", time.time())
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    # --- model call ---------------------------------------------------------
    def wrap_model_call(self, request, handler):  # noqa: ANN001
        self._step += 1
        step = self._step
        started = time.perf_counter()
        error: str | None = None
        try:
            response = handler(request)
        except Exception as exc:  # noqa: BLE001 - trace then re-raise
            error = f"{type(exc).__name__}: {exc}"
            self._write(
                {
                    "event": "model_call",
                    "step": step,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": _redact_text(error),
                }
            )
            raise
        self._write(
            {
                "event": "model_call",
                "step": step,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": None,
            }
        )
        return response

    async def awrap_model_call(self, request, handler):  # noqa: ANN001
        self._step += 1
        step = self._step
        started = time.perf_counter()
        response = await handler(request)
        self._write(
            {
                "event": "model_call",
                "step": step,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": None,
            }
        )
        return response

    # --- tool call ----------------------------------------------------------
    def wrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._write(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args_redacted": redact_args(args),
            }
        )
        started = time.perf_counter()
        error: str | None = None
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001 - trace then re-raise
            error = f"{type(exc).__name__}: {exc}"
            self._write(
                {
                    "event": "tool_result",
                    "step": self._step,
                    "tool": name,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": _redact_text(error),
                }
            )
            raise
        content = getattr(result, "content", None)
        self._write(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "result": _redact_value(content) if content is not None else None,
                "error": None,
            }
        )
        return result

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._write(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args_redacted": redact_args(args),
            }
        )
        started = time.perf_counter()
        result = await handler(request)
        content = getattr(result, "content", None)
        self._write(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "result": _redact_value(content) if content is not None else None,
                "error": None,
            }
        )
        return result

    # --- run end ------------------------------------------------------------
    def after_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        self._write({"event": "run_end", "steps": self._step})
        return None

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.after_agent(state, runtime)


def build_trace_middleware(
    run_id: str, trace_dir: str = ".traces", enabled: bool = True
) -> TraceMiddleware:
    return TraceMiddleware(run_id, trace_dir=trace_dir, enabled=enabled)


__all__ = ["TraceMiddleware", "build_trace_middleware", "redact_args"]
