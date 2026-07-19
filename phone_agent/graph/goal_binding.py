"""Canonical task binding shared by Goal compilation and requirement extraction."""

from __future__ import annotations

import hashlib
import unicodedata

TASK_BINDING_NORMALIZER_VERSION = "task_binding_nfkc_casefold_v1"


def normalize_task_binding(task: str) -> str:
    """Return the canonical representation used only for runtime binding."""

    normalized = unicodedata.normalize("NFKC", str(task or ""))
    return " ".join(normalized.casefold().split())


def compute_task_binding(task: str, *, length: int = 16) -> str:
    """Compute an internal binding digest that must never enter projections."""

    return hashlib.sha256(normalize_task_binding(task).encode("utf-8")).hexdigest()[
        :length
    ]
