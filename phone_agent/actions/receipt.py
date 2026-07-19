"""Dispatch receipts emitted by Execute without claiming transition success."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from phone_agent.actions.capability import ToolCapability
DispatchStatus = Literal["accepted", "rejected", "unknown"]


@dataclass(frozen=True)
class ActionReceipt:
    """Evidence that dispatch was accepted, rejected, or became uncertain."""

    dispatch_status: DispatchStatus
    capability_id: str
    capability_version: str
    invocation_id: str
    correlation_id: str
    side_effect_receipt: dict[str, Any] | None
    retry_safety: str

    @classmethod
    def create(
        cls,
        capability: ToolCapability,
        dispatch_status: DispatchStatus,
        *,
        correlation_id: str | None = None,
        side_effect_receipt: dict[str, Any] | None = None,
    ) -> "ActionReceipt":
        """Create a receipt with fresh invocation and correlation identifiers."""

        invocation_id = uuid4().hex
        return cls(
            dispatch_status=dispatch_status,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            invocation_id=invocation_id,
            correlation_id=correlation_id or invocation_id,
            side_effect_receipt=side_effect_receipt,
            retry_safety=capability.retry_safety,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the receipt for graph state and checkpoint storage."""

        return asdict(self)
