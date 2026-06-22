"""Selector schema validation for non-executable IntentIR metadata."""

from __future__ import annotations

from typing import Any
import re


OBJECT_FILTER_ALLOWED_KEYS = {
    "object_type",
    "role",
    "source",
    "list_id",
    "title_hash_prefix",
    "text_hash_prefix",
    "resource_id_hash_prefix",
    "lineage_hash_prefix",
}
OBJECT_FILTER_HASH_KEYS = {
    "title_hash_prefix",
    "text_hash_prefix",
    "resource_id_hash_prefix",
    "lineage_hash_prefix",
}


def validate_object_filter(value: Any) -> dict[str, str]:
    """Validate strict flat object_filter v1."""

    if not isinstance(value, dict) or not value:
        raise ValueError("object_filter must be a non-empty object")
    extras = set(value) - OBJECT_FILTER_ALLOWED_KEYS
    if extras:
        raise ValueError(f"unsupported object_filter fields: {sorted(extras)}")
    result: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(raw, str):
            raise ValueError(f"object_filter.{key} must be a string")
        item = raw.strip()
        if not item or len(item) > 64:
            raise ValueError(f"object_filter.{key} must be 1-64 chars")
        if any(char in item for char in "*?[](){}|\\"):
            raise ValueError(f"object_filter.{key} must not contain regex syntax")
        if key in OBJECT_FILTER_HASH_KEYS and not re.fullmatch(r"[0-9a-fA-F]{6,16}", item):
            raise ValueError(f"object_filter.{key} must be a 6-16 char hex prefix")
        result[key] = item
    return result
