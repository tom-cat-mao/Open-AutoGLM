"""Backend-independent GestureIR compiler.

GestureIR deliberately keeps 0-1000 relative coordinates. Existing graph tools
remain the single relative->absolute conversion owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from phone_agent.actions.validator import validate_action


GestureKind = Literal["tap", "long_press", "swipe", "type", "key", "wait", "launch"]


@dataclass(frozen=True)
class GestureIR:
    kind: GestureKind
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_action_to_gesture(action: dict[str, Any]) -> GestureIR:
    """Compile canonical ActionIR dict to relative GestureIR."""

    action = validate_action(action)
    if action.get("_metadata") == "finish":
        return GestureIR("wait", {"duration": "0 seconds", "finish": True})
    name = action.get("action")
    if name in {"Tap", "Double Tap"}:
        return GestureIR("tap", {"element": list(action["element"]), "tap_count": 2 if name == "Double Tap" else 1})
    if name == "Long Press":
        return GestureIR("long_press", {"element": list(action["element"]), "duration_ms": 3000})
    if name == "Swipe":
        return GestureIR("swipe", {"start": list(action["start"]), "end": list(action["end"])})
    if name in {"Type", "Type_Name"}:
        return GestureIR("type", {"text": action["text"]})
    if name in {"Back", "Home"}:
        return GestureIR("key", {"key": name.lower()})
    if name == "Launch":
        return GestureIR("launch", {"app": action["app"]})
    if name == "Wait":
        return GestureIR("wait", {"duration": action["duration"]})
    return GestureIR("wait", {"duration": "0 seconds", "non_device_action": name})
