"""Screen mark provider interfaces and implementations."""

from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProvider,
    MarkProviderHint,
    MarkProviderResult,
    ScreenBinding,
)

__all__ = [
    "FakeGroundingProvider",
    "MarkCandidate",
    "MarkProvider",
    "MarkProviderHint",
    "MarkProviderResult",
    "ScreenBinding",
]
