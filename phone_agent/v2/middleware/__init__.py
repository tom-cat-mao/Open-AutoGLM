"""v2 middleware package: safety HITL, image pruning, and JSONL trace.

See ``docs/refactor-thin-loop-v2.md`` §9 for the binding contract.
"""

from __future__ import annotations

from phone_agent.v2.middleware.images import (
    ImagePruningMiddleware,
    build_image_middleware,
)
from phone_agent.v2.middleware.safety import (
    build_hitl_middleware,
    is_sensitive_tool_call,
)
from phone_agent.v2.middleware.trace import (
    TraceMiddleware,
    build_trace_middleware,
    redact_args,
)

__all__ = [
    "build_hitl_middleware",
    "is_sensitive_tool_call",
    "ImagePruningMiddleware",
    "build_image_middleware",
    "TraceMiddleware",
    "build_trace_middleware",
    "redact_args",
]
