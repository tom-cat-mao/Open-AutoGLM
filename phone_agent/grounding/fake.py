"""Deterministic grounding provider for unit tests and local dry runs."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from phone_agent.grounding.provider import GroundingResult, GroundingTarget, ScreenBinding


class FakeGroundingProvider:
    """Grounding provider returning configured 0-1000 boxes without MLX."""

    name = "fake"
    version = "test"

    def __init__(
        self,
        *,
        bbox: list[int] | None = None,
        failure_code: str | None = None,
        provider_input_hash: str | None = None,
    ) -> None:
        self.bbox = bbox or [400, 400, 600, 600]
        self.failure_code = failure_code
        self.provider_input_hash = provider_input_hash
        self.requests: list[dict[str, Any]] = []

    def ground(
        self,
        screenshot: Any,
        target: GroundingTarget,
        screen_binding: ScreenBinding,
        timeout: float | None = None,
    ) -> GroundingResult:
        started = time.perf_counter()
        input_hash = self.provider_input_hash or hashlib.sha256(
            str(getattr(screenshot, "base64_data", "")).encode("utf-8")
        ).hexdigest()[:16]
        self.requests.append(
            {
                "target": target.redacted_summary(),
                "screen_binding": screen_binding.to_dict(),
                "timeout": timeout,
                "provider_input_hash": input_hash,
            }
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if self.failure_code:
            return GroundingResult(
                success=False,
                provider=self.name,
                failure_code=self.failure_code,
                message=self.failure_code,
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=input_hash,
                latency_ms=latency_ms,
            )
        x1, y1, x2, y2 = self.bbox
        return GroundingResult(
            success=True,
            provider=self.name,
            bbox=list(self.bbox),
            center=[int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))],
            confidence=1.0,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=input_hash,
            latency_ms=latency_ms,
        )
