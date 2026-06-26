"""Screen mark provider interfaces and implementations."""

from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProvider,
    MarkProviderHint,
    MarkProviderResult,
    ScreenBinding,
)

__all__ = [
    "AccessibilityTreeProvider",
    "FallbackMarkProvider",
    "FakeGroundingProvider",
    "RemoteOpenAIGroundingProvider",
    "MarkCandidate",
    "MarkProvider",
    "MarkProviderHint",
    "MarkProviderResult",
    "ScreenBinding",
]
