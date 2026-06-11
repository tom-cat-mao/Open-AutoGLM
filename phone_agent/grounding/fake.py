"""Deterministic mark provider for unit tests and local dry runs."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding


class FakeGroundingProvider:
    """Mark provider returning configured 0-1000 boxes without MLX."""

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

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
    ) -> MarkProviderResult:
        started = time.perf_counter()
        input_hash = self.provider_input_hash or hashlib.sha256(
            str(getattr(screenshot, "base64_data", "")).encode("utf-8")
        ).hexdigest()[:16]
        self.requests.append(
            {
                "hints": [hint.redacted_summary() for hint in hints or []],
                "screen_binding": screen_binding.to_dict(),
                "timeout": timeout,
                "provider_input_hash": input_hash,
            }
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if self.failure_code:
            return MarkProviderResult(
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
            MarkCandidate(
                mark_id=f"fake_{index}",
                bbox=list(box),
                center=[int(round((box[0] + box[2]) / 2)), int(round((box[1] + box[3]) / 2))],
                confidence=1.0,
                source=self.name,
                valid=True,
                role="target",
                text_summary=(hints or [None])[0].description() if hints else "fake target",
            )
            for index, box in enumerate(boxes, start=1)
        ]
        status = "success" if candidates else "no_marks"
        return MarkProviderResult(
            success=bool(candidates),
            provider=self.name,
            failure_code=None if candidates else "grounding_no_candidate",
            message=None if candidates else "no mark candidates",
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=input_hash,
            latency_ms=latency_ms,
            marks=candidates,
            candidates=candidates,
            candidate_count=len(candidates),
            status=status,
            hints=[hint.redacted_summary() for hint in hints or []],
        )
