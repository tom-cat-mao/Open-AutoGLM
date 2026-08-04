"""Local JSONL tracing utilities for graph runs."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

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
    "mark_prompt_block",
    "objects_block",
    "prompt_blocks",
    "reflection_context",
    "reflect_request_messages",
    "request_messages",
    "target_text_hint",
    "text_hint",
}
SEMANTIC_DIGEST_KEYS = {
    "sha256",
    "task_hash",
    "entities_sha",
    "target_entity_hashes",
    "constraint_hashes",
    "description_sha256",
    "selected_object_id_hash",
    "object_evidence_hash",
    "title_stub",
    "title_hash",
    "container_lineage_hash",
    "list_lineage_hash",
}
RAW_DEBUG_KEYS = {
    "raw_model_response",
    "raw_model_tool_calls",
}
RAW_REQUEST_DEBUG_KEYS = {
    "prompt_blocks",
    "reflect_request_messages",
    "request_messages",
}


def _redacted_text(value: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(value),
    }


def sanitize_for_trace(
    value: Any,
    redact: bool = True,
    key: str | None = None,
    allow_raw_debug: bool = False,
    allow_raw_request_debug: bool = False,
) -> Any:
    """Return a JSON-safe value with sensitive fields redacted."""
    normalized_key = key.lower() if key else ""
    if normalized_key in SEMANTIC_DIGEST_KEYS:
        return "<redacted-digest>"
    if normalized_key in RAW_REQUEST_DEBUG_KEYS:
        if allow_raw_request_debug:
            return value
        if isinstance(value, str):
            return _redacted_text(value)
        return "<redacted>"
    if normalized_key in RAW_DEBUG_KEYS:
        if allow_raw_debug:
            return value
        if isinstance(value, str):
            return _redacted_text(value)
        return "<redacted>"
    if normalized_key in SENSITIVE_KEYS:
        return "<redacted>"
    if normalized_key in PRIVATE_TEXT_KEYS and isinstance(value, str):
        # Dangerous debug mode (trace_unredacted_prompt) exists precisely so
        # failures are debuggable; an error ``message`` that is always
        # redacted hides the one artifact that explains the crash.
        if allow_raw_request_debug:
            return value
        return _redacted_text(value)
    if isinstance(value, str) and re.search(
        r"(?<!hmac-)sha256:[0-9a-fA-F]{6,64}", value
    ):
        return _redacted_text(value)
    if isinstance(value, dict):
        return {
            str(k): sanitize_for_trace(
                v,
                redact,
                str(k),
                allow_raw_debug,
                allow_raw_request_debug,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_for_trace(
                item,
                redact,
                key,
                allow_raw_debug,
                allow_raw_request_debug,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            sanitize_for_trace(
                item,
                redact,
                key,
                allow_raw_debug,
                allow_raw_request_debug,
            )
            for item in value
        ]
    if not redact:
        return value
    return value


class JsonlTraceWriter:
    """Best-effort local JSONL trace writer."""

    def __init__(
        self,
        trace_id: str | None = None,
        trace_dir: str | Path = ".traces",
        redact: bool = True,
        allow_raw_debug: bool = False,
        allow_raw_request_debug: bool = False,
        strict: bool = False,
    ) -> None:
        self.trace_id = trace_id or str(uuid.uuid4())
        self.trace_dir = Path(trace_dir)
        self.redact = redact
        self.allow_raw_debug = allow_raw_debug
        self.allow_raw_request_debug = allow_raw_request_debug
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
                "payload": sanitize_for_trace(
                    payload or {},
                    self.redact,
                    allow_raw_debug=self.allow_raw_debug,
                    allow_raw_request_debug=self.allow_raw_request_debug,
                ),
            }
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            if self.strict:
                raise


def save_debug_screenshot(
    config: RunnableConfig,
    state: dict[str, Any],
    source: str,
    screenshot_b64: str | None,
) -> None:
    """Debug-full mode: persist the raw screenshot next to the trace (P0#10 exempt).

    Traces never embed raw screenshots (SENSITIVE_KEYS stays hard-redacted),
    which made grounding diagnoses blind. When ``configurable["debug_full"]``
    is set (PHONE_AGENT_DEBUG_FULL), each captured frame is written to
    ``<trace_dir>/screenshots/step_NNN_<source>.png`` and a
    ``debug_screenshot`` event records the on-disk path. No-op otherwise.
    """

    configurable = config.get("configurable", {}) if config else {}
    if not configurable.get("debug_full"):
        return
    writer = configurable.get("trace_writer")
    if writer is None or not getattr(writer, "enabled", False):
        return
    if not screenshot_b64:
        return
    import base64

    try:
        raw = base64.b64decode(screenshot_b64)
    except Exception:
        return
    step = int(state.get("step_count") or 0)
    try:
        shots_dir = writer.trace_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        path = shots_dir / f"step_{step:03d}_{source}.png"
        path.write_bytes(raw)
    except Exception:
        if getattr(writer, "strict", False):
            raise
        return
    emit_trace(
        config,
        state,
        "trace",
        "debug_screenshot",
        {"path": str(path), "source": source, "bytes": len(raw)},
    )


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
