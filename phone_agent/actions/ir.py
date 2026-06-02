"""Canonical action IR helpers.

The public graph boundary still accepts plain dictionaries for compatibility,
but this module gives the parser/adapter/validator/safety pipeline a typed
contract to convert through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, Union


class DoActionDict(TypedDict, total=False):
    _metadata: Literal["do"]
    action: str
    element: list[int | float]
    start: list[int | float]
    end: list[int | float]
    text: str
    message: str
    app: str
    duration: str


class FinishActionDict(TypedDict):
    _metadata: Literal["finish"]
    message: str


ActionDict = Union[DoActionDict, FinishActionDict]


@dataclass(frozen=True)
class ActionIR:
    """Typed representation of the canonical action dict."""

    metadata: Literal["do", "finish"]
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, action: dict[str, Any]) -> "ActionIR":
        metadata = action.get("_metadata")
        if metadata not in {"do", "finish"}:
            raise ValueError("ActionIR metadata must be do or finish")
        fields = {key: value for key, value in action.items() if key != "_metadata"}
        return cls(metadata=metadata, fields=fields)

    def to_dict(self) -> dict[str, Any]:
        return {**self.fields, "_metadata": self.metadata}


def to_action_ir(action: dict[str, Any] | ActionIR) -> ActionIR:
    """Coerce a compatible action object to ActionIR."""

    if isinstance(action, ActionIR):
        return action
    return ActionIR.from_dict(action)


def to_action_dict(action: dict[str, Any] | ActionIR) -> dict[str, Any]:
    """Return a plain dict for graph/tool compatibility."""

    if isinstance(action, ActionIR):
        return action.to_dict()
    return dict(action)
