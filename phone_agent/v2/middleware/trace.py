"""JSONL trace middleware: redacted, per-run observability.

Per AGENTS.md (P0 #6, egress redaction): every model call and
tool call is appended as a JSONL event to ``<trace_dir>/<run_id>.jsonl``.

Redaction rules (applied to every logged text value):
  * text values longer than 64 chars are truncated (``…`` suffix, original
    length recorded);
  * ``html`` tool arguments are omitted completely (only UTF-8 byte length);
  * sensitive substrings (phone/email/order/captcha/api-key/JWT/…) are replaced
    via :func:`phone_agent.config.redact.redact_context_text`;
  * screenshot ``base64`` is never logged — only ``screen_seq`` and byte length.

Events: ``run_start`` / ``capability_snapshot`` / ``model_call`` / ``tool_call``
/ ``tool_result`` / ``run_end`` / ``recall_evaluation`` with a timestamp, step
index, tool name, redacted args, latency (ms), and error text as applicable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

from langchain.agents.middleware import AgentMiddleware

from phone_agent.v2.middleware._redact import (
    redact_text as _redact_context_text,
    redact_value_no_base64,
)

_MAX_TEXT_CHARS = 64


def _redact_text(value: str) -> str:
    """P0 #6 trace policy: sensitive-redact, then cap at 64 chars (``…`` suffix)."""
    redacted = _redact_context_text(value)
    if len(redacted) > _MAX_TEXT_CHARS:
        return redacted[:_MAX_TEXT_CHARS] + "…"
    return redacted


def _redact_value(value: Any) -> Any:
    """Recursively redact a JSON-able value; drop image base64 payloads.

    Delegates the recursion + base64-drop to the shared
    :func:`phone_agent.v2.middleware._redact.redact_value_no_base64`, applying
    the trace-specific 64-char truncation via ``_redact_text``.
    """
    return redact_value_no_base64(value, _redact_text)


def redact_args(args: Any) -> Any:
    """Redact tool args while omitting HTML deliverable bodies entirely."""

    if not isinstance(args, Mapping):
        return _redact_value(args)
    redacted: dict[Any, Any] = {}
    for key, value in args.items():
        if str(key).casefold() == "html" and isinstance(value, str):
            redacted[key] = {
                "type": "text",
                "omitted": True,
                "bytes": len(value.encode("utf-8", errors="replace")),
            }
        else:
            redacted[key] = _redact_value(value)
    return redacted


def _tool_artifact(result: Any) -> Any:
    """Return a tool artifact, including tuple-style test integrations."""

    artifact = getattr(result, "artifact", None)
    if artifact is None and isinstance(result, tuple) and len(result) == 2:
        artifact = result[1]
    return artifact


class TraceMiddleware(AgentMiddleware):
    """Append redacted model/tool events to a per-run JSONL trace file."""

    def __init__(
        self,
        run_id: str,
        trace_dir: str = ".traces",
        enabled: bool = True,
        *,
        experience_writer: Any | None = None,
        session: Any | None = None,
        alias_overwrite_enabled: bool = True,
        alias_overwrite_notes: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.trace_dir = trace_dir
        self.enabled = enabled
        self._step = 0
        self._launched_apps: set[str] = set()
        self._path: str | None = None
        # The experience sink is independent of production trace enablement.
        # It receives only a fixed allowlist after the tool has returned and is
        # fail-open, so it cannot alter the actor or the device action result.
        self._experience_writer = experience_writer
        self._session = session
        self._alias_overwrite_enabled = bool(alias_overwrite_enabled)
        self._alias_overwrite_notes = tuple(
            str(item).strip() for item in alias_overwrite_notes if str(item).strip()
        )
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

    def _write_experience(
        self,
        tool: str,
        result: Any = None,
        *,
        args: Any = None,
        error: BaseException | None = None,
        launched_before: int = 0,
    ) -> None:
        """Persist a fixed-schema tool receipt without touching its return."""

        writer = self._experience_writer
        if writer is None:
            return
        try:
            from phone_agent.v2.experience import classify_tool_result

            launched = list(getattr(self._session, "launched_apps", []) or [])
            app_package = (
                launched[-1]
                if tool == "launch_app" and len(launched) > launched_before
                else None
            )
            call_args = args if isinstance(args, Mapping) else {}
            writer.append_event(
                run_id=self.run_id,
                step=self._step,
                ts=time.time(),
                tool=tool,
                result_class=classify_tool_result(result, error),
                app_package=app_package,
                device_scope=getattr(self, "experience_device_scope", "device:unknown"),
                intent=call_args.get("intent"),
                note=call_args.get("note"),
            )
        except Exception:  # noqa: BLE001 - observability must never alter actor behavior
            return

    def _write_alias_evidence(
        self,
        tool: str,
        args: Any,
        result: Any = None,
        *,
        error: BaseException | None = None,
        launched_before: int = 0,
    ) -> None:
        """Persist bounded dream evidence without storing a full model note."""

        if not self._alias_overwrite_enabled or tool not in {"launch_app", "back"}:
            return
        store = getattr(self._session, "app_store", None)
        record = getattr(store, "record_alias_tool_event", None)
        if not callable(record):
            return
        try:
            from phone_agent.config.redact import SENSITIVE_PATTERN
            from phone_agent.v2.experience import classify_tool_result

            call_args = args if isinstance(args, Mapping) else {}
            note = str(call_args.get("note", "") or "")
            folded_note = note.casefold()
            marker = next(
                (
                    candidate
                    for candidate in self._alias_overwrite_notes
                    if candidate.casefold() in folded_note
                ),
                None,
            )
            launched = list(getattr(self._session, "launched_apps", []) or [])
            package = (
                launched[-1]
                if tool == "launch_app" and len(launched) > launched_before
                else None
            )
            success = classify_tool_result(result, error) == "ok"
            term = (
                str(call_args.get("app_name", "") or "").strip()
                if tool == "launch_app"
                else ""
            )
            if term and SENSITIVE_PATTERN.search(term):
                term = ""
            record(
                run_id=self.run_id,
                step=self._step,
                tool=tool,
                term=term or None,
                package=package,
                success=success,
                note_marker=marker,
            )
        except Exception:  # noqa: BLE001 - memory evidence never changes execution
            return

    @property
    def launched_apps(self) -> set[str]:
        """Packages confirmed by successful launch-tool receipts this run."""

        return set(self._launched_apps)

    def reset_run_observations(self) -> None:
        """Reset per-run observations when a ThinPhoneAgent is reused."""

        self._launched_apps.clear()

    def set_experience_writer(self, writer: Any | None) -> None:
        """Attach or clear the current run's optional experience sink."""

        self._experience_writer = writer

    def record_event(self, event: str, **payload: Any) -> None:
        """Write a custom event through the same P0 redaction boundary."""

        redacted = _redact_value(payload)
        self._write({"event": str(event), **redacted})

    def _record_successful_launch(self, name: str, content: Any) -> None:
        if name != "launch_app":
            return
        from phone_agent.v2.recall import extract_launched_apps

        self._launched_apps.update(extract_launched_apps(content))

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
        launched_before = len(getattr(self._session, "launched_apps", []) or [])
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
            self._write_experience(
                name, error=exc, args=args, launched_before=launched_before
            )
            self._write_alias_evidence(
                name, args, error=exc, launched_before=launched_before
            )
            raise
        content = getattr(result, "content", None)
        self._record_successful_launch(name, content)
        artifact = _tool_artifact(result)
        self._write(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "result": _redact_value(content) if content is not None else None,
                "artifact": _redact_value(artifact) if artifact is not None else None,
                "error": None,
            }
        )
        self._write_experience(name, result, args=args, launched_before=launched_before)
        self._write_alias_evidence(name, args, result, launched_before=launched_before)
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
        launched_before = len(getattr(self._session, "launched_apps", []) or [])
        try:
            result = await handler(request)
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
            self._write_experience(
                name, error=exc, args=args, launched_before=launched_before
            )
            self._write_alias_evidence(
                name, args, error=exc, launched_before=launched_before
            )
            raise
        content = getattr(result, "content", None)
        self._record_successful_launch(name, content)
        artifact = _tool_artifact(result)
        self._write(
            {
                "event": "tool_result",
                "step": self._step,
                "tool": name,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "result": _redact_value(content) if content is not None else None,
                "artifact": _redact_value(artifact) if artifact is not None else None,
                "error": None,
            }
        )
        self._write_experience(name, result, args=args, launched_before=launched_before)
        self._write_alias_evidence(name, args, result, launched_before=launched_before)
        return result

    # --- run end ------------------------------------------------------------
    def after_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        self._write({"event": "run_end", "steps": self._step})
        return None

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:  # noqa: ANN001
        return self.after_agent(state, runtime)


def build_trace_middleware(
    run_id: str,
    trace_dir: str = ".traces",
    enabled: bool = True,
    *,
    experience_writer: Any | None = None,
    session: Any | None = None,
    alias_overwrite_enabled: bool = True,
    alias_overwrite_notes: tuple[str, ...] = (),
) -> TraceMiddleware:
    return TraceMiddleware(
        run_id,
        trace_dir=trace_dir,
        enabled=enabled,
        experience_writer=experience_writer,
        session=session,
        alias_overwrite_enabled=alias_overwrite_enabled,
        alias_overwrite_notes=alias_overwrite_notes,
    )


__all__ = ["TraceMiddleware", "build_trace_middleware", "redact_args"]
