"""v2 middleware package: safety HITL, image pruning, and JSONL trace.

See ``docs/refactor-thin-loop-v2.md`` §9 for the binding contract.
"""

from __future__ import annotations

from phone_agent.v2.middleware.budget import (
    BudgetMiddleware,
    build_budget_middleware,
)
from phone_agent.v2.middleware.images import (
    ContextPruningMiddleware,
    ImagePruningMiddleware,
    build_context_pruning_middleware,
    build_image_middleware,
)
from phone_agent.v2.middleware.safety import (
    build_hitl_middleware,
    is_sensitive_tool_call,
)
from phone_agent.v2.middleware.taskdoc import (
    TaskDocMiddleware,
    build_taskdoc_middleware,
)
from phone_agent.v2.middleware.trace import (
    TraceMiddleware,
    build_trace_middleware,
    redact_args,
)

__all__ = [
    "ToolCallVerdict",
    "classify_tool_call",
    "build_hitl_middleware",
    "build_safety_reviewer",
    "is_sensitive_tool_call",
    "BudgetMiddleware",
    "build_budget_middleware",
    "ContextPruningMiddleware",
    "build_context_pruning_middleware",
    "ImagePruningMiddleware",
    "build_image_middleware",
    "TaskDocMiddleware",
    "build_taskdoc_middleware",
    "TraceMiddleware",
    "build_trace_middleware",
    "redact_args",
]
