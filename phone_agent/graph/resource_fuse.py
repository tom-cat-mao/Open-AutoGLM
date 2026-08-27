"""Resource fuse helpers for graph routing and terminal node updates."""

from __future__ import annotations

import time
from typing import Any

from phone_agent.graph.trace import emit_trace


def resource_fuse_exhausted(state: dict[str, Any]) -> bool:
    step_cap = int(state.get("step_cap") or state.get("max_steps") or 0)
    if step_cap > 0 and int(state.get("step_count") or 0) >= step_cap:
        return True
    wall_clock_cap = state.get("wall_clock_cap_seconds")
    started_at = state.get("wall_clock_cap_started_at")
    try:
        cap = float(wall_clock_cap) if wall_clock_cap is not None else 0.0
        started = float(started_at) if started_at is not None else 0.0
    except (TypeError, ValueError):
        return False
    return cap > 0 and started > 0 and time.time() - started >= cap


def resource_fuse_update(state: dict[str, Any], config=None) -> dict[str, Any]:
    """Return a terminal update if a resource fuse is exhausted."""

    if not resource_fuse_exhausted(state):
        return {}
    payload = {
        "step_count": state.get("step_count"),
        "step_cap": state.get("step_cap") or state.get("max_steps"),
        "wall_clock_cap_seconds": state.get("wall_clock_cap_seconds"),
    }
    if config is not None:
        emit_trace(config, state, "graph", "resource_fuse_exhausted", payload)
    return {
        "finished": True,
        "failure_cause": "resource_fuse_exhausted",
        "finish_source": "resource_fuse_exhausted",
        "action_result": {
            "success": False,
            "message": "resource fuse exhausted",
        },
    }
