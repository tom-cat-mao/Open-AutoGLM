"""Reusable web-console event production for in-process agent runs.

The event dictionaries in this module are the IPC contract between the runner
process and :mod:`phone_agent.web.bridge`.  Sinks only need a ``put(event)``
method, which keeps the middleware usable with both ``queue.Queue`` in tests
and the flushed JSONL writer used by the runner.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config

from phone_agent.v2.agent import RunResult
from phone_agent.v2.middleware._redact import redact_text
from phone_agent.v2.middleware._tokens import estimate_message_tokens, usage_tokens
from phone_agent.v2.middleware.trace import redact_args

OBS_RE = re.compile(r"\[OBS\]\s+app=(?P<app>.*?)\s+screen#(?P<seq>\d+)")
_FAIL_PREFIXES = (
    "error:",
    "错误",
    "失败",
    "denied:",
    "未定位",
    "定位失败",
    "未写入（输入无效）",
    "未写入（校验失败）",
    "⚠️ 已拦截（未执行）",
)
_SAFETY_MARKERS = ("⚠️ 已拦截（未执行）", "confirm_irreversible=true")


class EventSink(Protocol):
    """Minimal sink shared by queues and append-only event files."""

    def put(self, event: dict[str, Any]) -> None: ...


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
    ).strip()


def _image_url(block: Any) -> str:
    if not isinstance(block, dict) or block.get("type") not in {
        "image",
        "image_url",
    }:
        return ""
    payload = block.get("image_url", block.get("source", ""))
    if isinstance(payload, dict):
        return str(payload.get("url", payload.get("data", "")) or "")
    return str(payload or "")


def _response_message(response: Any) -> Any | None:
    result = getattr(response, "result", None)
    messages = result if isinstance(result, list) else [response]
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai" or getattr(
            message, "tool_calls", None
        ):
            return message
    return messages[-1] if messages and messages[-1] is not None else None


def _safe_result_text(content: Any, limit: int = 1200) -> str:
    text = redact_text(_message_text(content)).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _result_ok(result: Any, text: str, error: str | None = None) -> bool:
    if error or str(getattr(result, "status", "")).lower() == "error":
        return False
    normalized = text.strip().lower()
    return not any(normalized.startswith(prefix.lower()) for prefix in _FAIL_PREFIXES)


def _taskdoc_from_messages(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        text = _message_text(message)
        marker = "[TASK_DOC]"
        if marker in text:
            return text[text.index(marker) + len(marker) :].strip()
    return None


class WebEventMiddleware(AgentMiddleware):
    """Publish the established compact event stream to a pluggable sink."""

    def __init__(self, events: EventSink) -> None:
        super().__init__()
        self.events = events
        self._step = 0
        self._tokens = 0
        self._last_taskdoc: str | None = None
        self._last_screen_key: tuple[str, Any] | None = None
        self._session: Any | None = None
        self._stop_requested = threading.Event()

    def attach_session(self, session: Any) -> None:
        self._session = session

    def request_stop(self) -> None:
        self._stop_requested.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    @property
    def step(self) -> int:
        return self._step

    @property
    def tokens(self) -> int:
        return self._tokens

    def emit(self, event: dict[str, Any]) -> None:
        """Publish a runner-owned lifecycle event through the same sink."""

        self._emit(event)

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        self.events.put(event)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        try:
            if self.stop_requested:
                if self._session is not None:
                    try:
                        self._session.takeover_reason = "用户从 Web 控制台停止"
                    except Exception:  # noqa: BLE001
                        pass
                self._emit({"event": "stopping", "step": self._step})
                return {"jump_to": "end"}
            messages = state.get("messages", []) if isinstance(state, dict) else []
            taskdoc = _taskdoc_from_messages(messages or [])
            if taskdoc is not None and taskdoc != self._last_taskdoc:
                self._last_taskdoc = taskdoc
                self._emit(
                    {
                        "event": "taskdoc_snapshot",
                        "step": self._step + 1,
                        "text": taskdoc,
                    }
                )
            for message in reversed(messages or []):
                if self._emit_screen_from_content(getattr(message, "content", None)):
                    break
        except Exception:  # noqa: BLE001
            pass
        return None

    async def abefore_model(
        self, state, runtime
    ) -> dict[str, Any] | None:  # noqa: ANN001
        return self.before_model(state, runtime)

    def _record_model(self, response: Any, latency_ms: int, error: str | None) -> None:
        self._step += 1
        message = _response_message(response) if response is not None else None
        turn_tokens = 0
        if message is not None:
            reported_tokens = usage_tokens(message)
            turn_tokens = (
                reported_tokens
                if reported_tokens is not None
                else estimate_message_tokens(message)
            )
        self._tokens += turn_tokens
        self._emit(
            {
                "event": "model_call",
                "step": self._step,
                "latency_ms": latency_ms,
                "tokens": turn_tokens,
                "tokens_total": self._tokens,
                "error": redact_text(error) if error else None,
            }
        )

    def wrap_model_call(self, request, handler):  # noqa: ANN001
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_model(
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_model(response, int((time.perf_counter() - started) * 1000), None)
        return response

    async def awrap_model_call(self, request, handler):  # noqa: ANN001
        started = time.perf_counter()
        try:
            response = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_model(
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_model(response, int((time.perf_counter() - started) * 1000), None)
        return response

    def _record_tool_result(
        self, name: str, result: Any, latency_ms: int, error: str | None
    ) -> None:
        content = getattr(result, "content", None) if result is not None else None
        text = _safe_result_text(content) if content is not None else ""
        ok = _result_ok(result, text, error)
        self._emit(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "text": text,
                "ok": ok,
                "latency_ms": latency_ms,
                "error": redact_text(error) if error else None,
            }
        )
        if text and any(marker in text for marker in _SAFETY_MARKERS):
            self._emit(
                {
                    "event": "safety_warning",
                    "step": self._step,
                    "tool": name,
                    "text": text,
                }
            )
        self._emit_screen_from_content(content)

    def _emit_screen_from_content(self, content: Any) -> bool:
        if not isinstance(content, list):
            return False
        text = _message_text(content)
        obs = OBS_RE.search(text)
        current_app = obs.group("app") if obs else None
        parsed_seq = int(obs.group("seq")) if obs else None
        for block in reversed(content):
            url = _image_url(block)
            if not url:
                continue
            screen_seq = block.get("screen_seq", parsed_seq)
            screen_key = (url, screen_seq)
            if screen_key == self._last_screen_key:
                return True
            self._last_screen_key = screen_key
            self._emit(
                {
                    "event": "screen",
                    "step": self._step,
                    "image": url,
                    "current_app": current_app,
                    "screen_seq": screen_seq,
                }
            )
            return True
        return False

    def wrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._emit(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args": redact_args(args),
            }
        )
        started = time.perf_counter()
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_tool_result(
                name,
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_tool_result(
            name, result, int((time.perf_counter() - started) * 1000), None
        )
        return result

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        self._emit(
            {
                "event": "tool_call",
                "step": self._step,
                "tool": name,
                "args": redact_args(args),
            }
        )
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._record_tool_result(
                name,
                None,
                int((time.perf_counter() - started) * 1000),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record_tool_result(
            name, result, int((time.perf_counter() - started) * 1000), None
        )
        return result

    def emit_run_end(self, result: RunResult, *, status: str) -> None:
        self._emit(
            {
                "event": "run_end",
                "status": status,
                "result": asdict(result),
                "tokens_total": self._tokens,
            }
        )


def terminal_status(result: RunResult, agent: Any) -> str:
    if result.success:
        return "succeeded"
    if result.reason == "token_budget_exhausted":
        return "budget_exhausted"
    if result.reason == "loop_fuse":
        return "loop_fuse"
    if getattr(getattr(agent, "session", None), "takeover_reason", None):
        return "takeover"
    if str(result.reason).startswith("error:"):
        return "error"
    return "failed"


__all__ = ["OBS_RE", "WebEventMiddleware", "terminal_status"]
