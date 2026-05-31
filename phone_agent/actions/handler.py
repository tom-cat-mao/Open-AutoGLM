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
    print(f"Parsing action: {response}")
    try:
        response = response.strip()
        if response.startswith('do(action="Type"') or response.startswith(
            'do(action="Type_Name"'
        ):
            action_name = (
                "Type_Name" if response.startswith('do(action="Type_Name"') else "Type"
            )
            text = response.split("text=", 1)[1][1:-2]
            action = {"_metadata": "do", "action": action_name, "text": text}
            return action
        elif response.startswith("do"):
            # Use AST parsing instead of eval for safety
            try:
                # Escape special characters (newlines, tabs, etc.) for valid Python syntax
                response = response.replace("\n", "\\n")
                response = response.replace("\r", "\\r")
                response = response.replace("\t", "\\t")

                tree = ast.parse(response, mode="eval")
                if not isinstance(tree.body, ast.Call):
                    raise ValueError("Expected a function call")

                call = tree.body
                if not isinstance(call.func, ast.Name) or call.func.id != "do":
                    raise ValueError("Expected do() action")
                # Extract keyword arguments safely
                action = {"_metadata": "do"}
                for keyword in call.keywords:
                    if keyword.arg is None:
                        raise ValueError("**kwargs are not supported")
                    key = keyword.arg
                    value = ast.literal_eval(keyword.value)
                    action[key] = value

                return action
            except (SyntaxError, ValueError) as e:
                raise ValueError(f"Failed to parse do() action: {e}")

        elif response.startswith("finish"):
            try:
                tree = ast.parse(response, mode="eval")
                if not isinstance(tree.body, ast.Call):
                    raise ValueError("Expected a function call")
                call = tree.body
                if not isinstance(call.func, ast.Name) or call.func.id != "finish":
                    raise ValueError("Expected finish() action")

                action = {"_metadata": "finish"}
                for keyword in call.keywords:
                    if keyword.arg is None:
                        raise ValueError("**kwargs are not supported")
                    action[keyword.arg] = ast.literal_eval(keyword.value)
            except (SyntaxError, ValueError) as e:
                raise ValueError(f"Failed to parse finish() action: {e}")
        else:
            raise ValueError(f"Failed to parse action: {response}")
        return action
    except Exception as e:
        raise ValueError(f"Failed to parse action: {e}")


def do(**kwargs) -> dict[str, Any]:
    """Helper function for creating 'do' actions."""
    kwargs["_metadata"] = "do"
    return kwargs


def finish(**kwargs) -> dict[str, Any]:
    """Helper function for creating 'finish' actions."""
    kwargs["_metadata"] = "finish"
    return kwargs
