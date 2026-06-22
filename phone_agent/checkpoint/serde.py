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

    return sanitize_context_payload(_collapse_sidecars_for_checkpoint(value), key, consumer="checkpoint")


def _collapse_sidecars_for_checkpoint(value: Any, key: str | None = None) -> Any:
    """Replace full structure/object sidecars with summaries before checkpoint egress."""

    if isinstance(value, dict):
        if key == "screen_structure":
            return _screen_structure_summary(value)
        if key == "screen_structures":
            return _screen_structures_summary(value)
        if key == "object_registry":
            return _object_registry_summary(value)
        collapsed = {}
        for child_key, child in value.items():
            if child_key == "observation" and isinstance(child, dict):
                child = _collapse_observation_sidecars(child)
            collapsed[child_key] = _collapse_sidecars_for_checkpoint(child, str(child_key))
        return collapsed
    if isinstance(value, list):
        return [_collapse_sidecars_for_checkpoint(item, key) for item in value]
    return value


def _collapse_observation_sidecars(value: dict[str, Any]) -> dict[str, Any]:
    collapsed = dict(value)
    if isinstance(collapsed.get("screen_structure"), dict):
        collapsed["screen_structure"] = _screen_structure_summary(collapsed["screen_structure"])
    if isinstance(collapsed.get("screen_structures"), list):
        collapsed["screen_structures"] = _screen_structures_summary(collapsed["screen_structures"])
    if isinstance(collapsed.get("object_registry"), dict):
        collapsed["object_registry"] = _object_registry_summary(collapsed["object_registry"])
    return collapsed


def _screen_structures_summary(value: list[Any]) -> dict[str, Any]:
    summaries = [_screen_structure_summary(item) for item in value if isinstance(item, dict)]
    kind_counts: dict[str, int] = {}
    for item in summaries:
        kind = str(item.get("structure_kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    return {
        "status": "ok" if summaries else "missing_sidecar",
        "structure_count": len(summaries),
        "kind_counts": kind_counts,
        "merge_order": [item.get("structure_kind") for item in summaries],
        "structures": summaries,
    }


def _screen_structure_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "screen_id": value.get("screen_id"),
        "semantic_screen_id": value.get("semantic_screen_id"),
        "mark_set_version": value.get("mark_set_version"),
        "topology_digest": value.get("topology_digest"),
        "status": value.get("status"),
        "structure_kind": value.get("structure_kind"),
        "source_provider": value.get("source_provider"),
        "confidence_tier": value.get("confidence_tier"),
        "structure_digest": value.get("structure_digest"),
        "node_count": value.get("node_count") or len(value.get("nodes") or {}),
        "root_node_id": value.get("root_node_id"),
    }


def _object_registry_summary(value: dict[str, Any]) -> dict[str, Any]:
    objects = value.get("objects") or {}
    counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    eligible_count = 0
    iterable = objects.values() if isinstance(objects, dict) else objects
    for item in iterable or []:
        if isinstance(item, dict):
            object_type = str(item.get("object_type") or "unknown")
            counts[object_type] = counts.get(object_type, 0) + 1
            source_kind = str(item.get("source_kind") or "unknown")
            source_counts[source_kind] = source_counts.get(source_kind, 0) + 1
            if item.get("executable_selector"):
                eligible_count += 1
    return {
        "screen_id": value.get("screen_id"),
        "semantic_screen_id": value.get("semantic_screen_id"),
        "object_set_version": value.get("object_set_version"),
        "structure_topology_digest": value.get("structure_topology_digest"),
        "mark_set_version": value.get("mark_set_version"),
        "status": value.get("status"),
        "object_count": value.get("object_count") or len(objects),
        "object_type_counts": counts,
        "source_kind_counts": source_counts,
        "eligible_selector_count": eligible_count,
        "truncation_summary": value.get("truncation_summary") or {},
    }


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
