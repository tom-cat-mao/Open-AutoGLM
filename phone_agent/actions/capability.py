"""Immutable capability declarations for canonical actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ImplementationStatus = Literal["implemented", "unavailable", "delegated"]
SideEffectKind = Literal["none", "device_local", "external", "user_delegated"]
ObservationEffect = Literal["none", "may_change_ui", "external"]
RetrySafety = Literal["safe", "idempotent", "unsafe", "unknown"]


@dataclass(frozen=True)
class ToolCapability:
    """Static execution and verification policy for one canonical action."""

    action_name: str
    implementation_status: ImplementationStatus
    side_effect_kind: SideEffectKind
    observation_effect: ObservationEffect
    required_postconditions: tuple[str, ...]
    retry_safety: RetrySafety
    hitl_policy_id: str | None = None
    can_advance_goal: bool = True
    version: str = "capability_gate_v1"

    @property
    def capability_id(self) -> str:
        """Return the stable capability identifier."""

        normalized = self.action_name.lower().replace(" ", "_")
        return f"phone_agent.action.{normalized}"

    @property
    def requires_reobservation(self) -> bool:
        """Return whether this action must pass through Reflect."""

        return self.observation_effect != "none" or self.can_advance_goal


def _device_capability(action_name: str, *, retry_safety: RetrySafety) -> ToolCapability:
    return ToolCapability(
        action_name=action_name,
        implementation_status="implemented",
        side_effect_kind="device_local",
        observation_effect="may_change_ui",
        required_postconditions=("expected_transition",),
        retry_safety=retry_safety,
    )


_CAPABILITIES = {
    capability.action_name: capability
    for capability in (
        _device_capability("Tap", retry_safety="unsafe"),
        _device_capability("Double Tap", retry_safety="unsafe"),
        _device_capability("Long Press", retry_safety="unsafe"),
        _device_capability("Swipe", retry_safety="unsafe"),
        _device_capability("Type", retry_safety="unsafe"),
        _device_capability("Type_Name", retry_safety="unsafe"),
        _device_capability("Back", retry_safety="unsafe"),
        _device_capability("Home", retry_safety="idempotent"),
        _device_capability("Launch", retry_safety="idempotent"),
        ToolCapability(
            action_name="Wait",
            implementation_status="implemented",
            side_effect_kind="none",
            observation_effect="may_change_ui",
            required_postconditions=("reobserve_after_wait",),
            retry_safety="safe",
        ),
        ToolCapability(
            action_name="Note",
            implementation_status="unavailable",
            side_effect_kind="none",
            observation_effect="none",
            required_postconditions=(),
            retry_safety="safe",
            can_advance_goal=False,
        ),
        ToolCapability(
            action_name="Locate",
            implementation_status="implemented",
            side_effect_kind="none",
            observation_effect="none",
            required_postconditions=(),
            retry_safety="safe",
            can_advance_goal=False,
        ),
        ToolCapability(
            action_name="Call_API",
            implementation_status="unavailable",
            side_effect_kind="external",
            observation_effect="external",
            required_postconditions=("external_acknowledgement",),
            retry_safety="unknown",
        ),
        ToolCapability(
            action_name="Interact",
            implementation_status="delegated",
            side_effect_kind="user_delegated",
            observation_effect="may_change_ui",
            required_postconditions=("user_acknowledgement", "expected_transition"),
            retry_safety="unknown",
            hitl_policy_id="takeover",
        ),
        ToolCapability(
            action_name="Take_over",
            implementation_status="delegated",
            side_effect_kind="user_delegated",
            observation_effect="may_change_ui",
            required_postconditions=("user_acknowledgement", "expected_transition"),
            retry_safety="unknown",
            hitl_policy_id="takeover",
        ),
    )
}


def get_tool_capability(action_name: str) -> ToolCapability | None:
    """Return the declaration for a canonical action, if one exists."""

    return _CAPABILITIES.get(action_name)


def get_all_capabilities() -> tuple[ToolCapability, ...]:
    """Return all capability declarations in deterministic action-name order."""

    return tuple(_CAPABILITIES[name] for name in sorted(_CAPABILITIES))

