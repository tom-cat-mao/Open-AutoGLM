"""Action types and parsing for AI model outputs.

This module provides:
- ActionResult: Dataclass for action execution results
- parse_action(): Safe AST-based parsing of model action strings
- do() / finish(): Helper functions for creating action dicts
"""

import ast
from dataclasses import dataclass
from typing import Any


@dataclass
class ActionResult:
    """Result of an action execution."""

    success: bool
    should_finish: bool
    message: str | None = None
    requires_confirmation: bool = False


def parse_action(response: str) -> dict[str, Any]:
    """
    Parse action from model response.

    Uses ast.parse + ast.literal_eval for safety (never eval).

    Args:
        response: Raw response string from the model.

    Returns:
        Parsed action dictionary with _metadata key ("do" or "finish").

    Raises:
        ValueError: If the response cannot be parsed.
    """
    try:
        response = response.strip()
        if response.startswith("do"):
            action = _parse_call_action(response, "do")
            action["_metadata"] = "do"
        elif response.startswith("finish"):
            action = _parse_call_action(response, "finish")
            action["_metadata"] = "finish"
        else:
            raise ValueError(f"Failed to parse action: {response}")
        return action
    except Exception as e:
        raise ValueError(f"Failed to parse action: {e}")


def _parse_call_action(response: str, expected_function: str) -> dict[str, Any]:
    """Safely parse a do()/finish() call with literal keyword arguments only."""
    try:
        tree = ast.parse(response, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python literal call syntax: {exc}") from exc

    if not isinstance(tree.body, ast.Call):
        raise ValueError("Expected a function call")

    call = tree.body
    if not isinstance(call.func, ast.Name) or call.func.id != expected_function:
        raise ValueError(f"Expected {expected_function}() action")
    if call.args:
        raise ValueError("Positional arguments are not supported")

    action: dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise ValueError("**kwargs are not supported")
        try:
            action[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"Keyword {keyword.arg} must be a literal value") from exc
    return action


def do(**kwargs) -> dict[str, Any]:
    """Helper function for creating 'do' actions."""
    kwargs["_metadata"] = "do"
    return kwargs


def finish(**kwargs) -> dict[str, Any]:
    """Helper function for creating 'finish' actions."""
    kwargs["_metadata"] = "finish"
    return kwargs
