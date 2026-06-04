"""Screen grounding provider interfaces and implementations."""

from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.provider import (
    GroundingProvider,
    GroundingResult,
    GroundingTarget,
    ScreenBinding,
)

__all__ = [
    "FakeGroundingProvider",
    "GroundingProvider",
    "GroundingResult",
    "GroundingTarget",
    "ScreenBinding",
]
