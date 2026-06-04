"""Deterministic grounding provider for unit tests and local dry runs."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from phone_agent.grounding.provider import GroundingCandidate, GroundingResult, GroundingTarget, ScreenBinding


class FakeGroundingProvider:
    """Grounding provider returning configured 0-1000 boxes without MLX."""

    name = "fake"
    version = "test"

    def __init__(
        self,
        *,
        bbox: list[int] | None = None,
        bboxes: list[list[int]] | None = None,
        failure_code: str | None = None,
        provider_input_hash: str | None = None,
    ) -> None:
        self.bbox = bbox or [400, 400, 600, 600]
        self.bboxes = bboxes
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
        boxes = self.bboxes or [self.bbox]
        candidates = [
            GroundingCandidate(
                bbox=list(box),
                center=[int(round((box[0] + box[2]) / 2)), int(round((box[1] + box[3]) / 2))],
                confidence=1.0,
                source=self.name,
                valid=True,
            )
            for box in boxes
        ]
        if len(candidates) != 1:
            return GroundingResult(
                success=False,
                provider=self.name,
                failure_code="grounding_ambiguous" if candidates else "grounding_no_candidate",
                message="candidate count must be exactly one",
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash=input_hash,
                latency_ms=latency_ms,
                candidates=candidates,
                candidate_count=len(candidates),
                grounding_status="grounding_ambiguous" if candidates else "grounding_no_candidate",
            )
        x1, y1, x2, y2 = candidates[0].bbox
        return GroundingResult(
            success=True,
            provider=self.name,
            bbox=list(candidates[0].bbox),
            center=[int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))],
            confidence=1.0,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=input_hash,
            latency_ms=latency_ms,
            candidates=candidates,
            candidate_count=1,
            grounding_status="success",
            selected_candidate_id=0,
        )
