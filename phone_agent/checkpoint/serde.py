"""Privacy-aware checkpoint serializer for LangGraph.

Wraps a LangGraph checkpoint serializer (default ``JsonPlusSerializer``) and
applies the **checkpoint** sanitization policy at dumps time: every string
value under a key listed in ``PRIVATE_CONTEXT_TEXT_KEYS`` is replaced with a
``{redacted, length, sha256}`` stub; all other strings are regex-redacted.

The wrapper is a no-op for loads (checkpoint bytes already contain stubs, and
``loads`` simply reconstructs the dict).  It does NOT change the in-memory
state that flows through the graph — state remains raw (or regex-redacted for
the screenshot→model→state→prompt reflection loop) during a run, and stubs
are produced only at checkpoint egress.

Usage::

    from langgraph.checkpoint.sqlite import SqliteSaver
    from phone_agent.checkpoint.serde import RedactingSerializer

    checkpointer = SqliteSaver(
        sqlite3.connect("checkpoints.db"),
        serde=RedactingSerializer(),
    )

The wrapper is defensive: if the inner serializer raises during dumps, the
original (un-redacted) value is never written — the exception propagates so
the caller can decide how to handle it.
"""

from __future__ import annotations

from typing import Any


def _get_default_serializer() -> Any:
    """Import JsonPlusSerializer lazily so this module stays importable in
    environments where ``langgraph.checkpoint`` is not installed (e.g. unit
    tests that only exercise the redaction helpers).
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer()


def _redact_for_checkpoint(value: Any, key: str | None = None) -> Any:
    """Apply checkpoint-policy sanitization to a value.

    Imported lazily to avoid a circular import at module load time (the
    helpers live in ``phone_agent.graph.context``).
    """
    from phone_agent.graph.context import sanitize_context_payload

    return sanitize_context_payload(value, key, consumer="checkpoint")


class RedactingSerializer:
    """LangGraph checkpoint serializer that redacts sensitive values at dumps.

    Parameters
    ----------
    inner:
        A LangGraph-compatible serializer implementing ``dumps`` / ``loads``
        (and optionally ``dumps_typed`` / ``loads_typed``).  Defaults to
        ``JsonPlusSerializer()``.
    """

    def __init__(self, inner: Any | None = None) -> None:
        self._inner = inner if inner is not None else _get_default_serializer()

    # --- core serde -----------------------------------------------------
    def dumps(self, value: Any) -> bytes:
        redacted = _redact_for_checkpoint(value)
        return self._inner.dumps(redacted)

    def loads(self, data: bytes) -> Any:
        return self._inner.loads(data)

    # --- typed variants (used by BaseCheckpointSaver implementations) ---
    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        redacted = _redact_for_checkpoint(value)
        if hasattr(self._inner, "dumps_typed"):
            return self._inner.dumps_typed(redacted)
        return ("json", self._inner.dumps(redacted))

    def loads_typed(self, payload: tuple[str, bytes]) -> Any:
        if hasattr(self._inner, "loads_typed"):
            return self._inner.loads_typed(payload)
        _type, data = payload
        return self._inner.loads(data)
