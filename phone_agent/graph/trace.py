"""Local JSONL tracing utilities for graph runs."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from phone_agent.graph.context import sanitize_context_payload

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "base64_data",
    "image_url",
    "prompt",
    "screenshot_b64",
    "secret",
    "text",
    "token",
}
PRIVATE_TEXT_KEYS = {
    "action_raw",
    "interrupt_message",
    "message",
    "reflection",
    "final_message",
    "error",
    "result_message_summary",
    "summary",
    "system_prompt",
    "task",
    "thinking",
    "visible_text",
    "observed_text",
    "parse_error",
    "context_block",
}


def _redacted_text(value: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }


def sanitize_for_trace(value: Any, redact: bool = True, key: str | None = None) -> Any:
    """Return a JSON-safe value with sensitive fields redacted."""
    if not redact:
        return value

    normalized_key = key.lower() if key else ""
    if normalized_key in SENSITIVE_KEYS:
        return "<redacted>"
    if normalized_key in PRIVATE_TEXT_KEYS and isinstance(value, str):
        return _redacted_text(value)
    if isinstance(value, dict):
        return {
            str(k): sanitize_context_payload(sanitize_for_trace(v, redact, str(k)), str(k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_context_payload(sanitize_for_trace(item, redact, key), key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_context_payload(sanitize_for_trace(item, redact, key), key) for item in value]
    return value


class JsonlTraceWriter:
    """Best-effort local JSONL trace writer."""

    def __init__(
        self,
        trace_id: str | None = None,
        trace_dir: str | Path = ".traces",
        redact: bool = True,
        strict: bool = False,
    ) -> None:
        self.trace_id = trace_id or str(uuid.uuid4())
        self.trace_dir = Path(trace_dir)
        self.redact = redact
        self.strict = strict
        self.path = self.trace_dir / f"{self.trace_id}.jsonl"
        self.enabled = True
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.enabled = False
            if self.strict:
                raise

    def emit(
        self,
        node: str,
        event: str,
        step_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append one trace event; swallow errors unless strict tracing is enabled."""
        if not self.enabled:
            return
        try:
            record = {
                "run_id": self.trace_id,
                "trace_id": self.trace_id,
                "step_id": step_id,
                "node": node,
                "event": event,
                "timestamp": time.time(),
                "payload": sanitize_for_trace(payload or {}, self.redact),
            }
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            if self.strict:
                raise


def emit_trace(
    config: RunnableConfig,
    state: dict[str, Any],
    node: str,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit a graph trace event from node code when tracing is configured."""
    configurable = config.get("configurable", {}) if config else {}
    writer = configurable.get("trace_writer")
    if writer is None:
        return
    writer.emit(
        node=node,
        event=event,
        step_id=int(state.get("step_count") or 0),
        payload=payload,
    )
