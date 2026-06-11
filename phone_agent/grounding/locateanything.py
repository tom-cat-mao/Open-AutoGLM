"""Optional LocateAnything-3B-4bit MLX mark provider."""

from __future__ import annotations

import base64
import hashlib
import platform
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from phone_agent.grounding.parser import GroundingParseError, parse_box_candidates
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding


DEFAULT_LOCATEANYTHING_MAX_SIZE = 960


class LocateAnythingMLXProvider:
    """Lazy MLX wrapper for LocateAnything hint-to-mark generation."""

    name = "locateanything_mlx"
    version = "3b-4bit"

    def __init__(
        self,
        model_path: str | Path = "models/LocateAnything-3B-4bit",
        *,
        max_size: int = DEFAULT_LOCATEANYTHING_MAX_SIZE,
    ) -> None:
        if max_size <= 0:
            raise ValueError("LocateAnything max_size must be positive")
        self.model_path = Path(model_path)
        self.max_size = max_size
        self._model = None
        self._processor = None

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
    ) -> MarkProviderResult:
        started = time.perf_counter()
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return self._failure("unsupported_platform", screen_binding, started)
        if not self.model_path.exists():
            return self._failure("model_not_found", screen_binding, started)
        descriptions = [hint.description() for hint in hints or [] if hint.description()]
        if not descriptions:
            return self._failure("missing_hint", screen_binding, started)
        all_candidates: list[MarkCandidate] = []
        provider_input_hash: str | None = None
        try:
            image, provider_input_hash = self._prepare_image(screenshot)
            for hint_index, description in enumerate(descriptions, start=1):
                output = self._run_model(image, description, timeout=timeout)
                parsed_set = parse_box_candidates(output)
                valid_candidates = parsed_set.valid_candidates
                candidates = [
                    MarkCandidate(
                        mark_id=f"la_{hint_index}_{index}",
                        bbox=box.bbox,
                        center=box.center,
                        confidence=None,
                        source=self.name,
                        valid=box.valid,
                        reason=box.reason,
                        role=(hints or [None])[hint_index - 1].role if hints and len(hints) >= hint_index else None,
                        text_summary=description,
                    )
                    for index, box in enumerate(parsed_set.candidates, start=1)
                ]
                if len(valid_candidates) > 1:
                    return self._failure(
                        "grounding_ambiguous",
                        screen_binding,
                        started,
                        message="multiple valid bboxes",
                        candidates=all_candidates + candidates,
                    )
                all_candidates.extend(candidates)
        except GroundingParseError as exc:
            return self._failure(exc.code, screen_binding, started, message=str(exc))
        except ImportError:
            return self._failure("import_error", screen_binding, started)
        except TimeoutError:
            return self._failure("timeout", screen_binding, started)
        except Exception as exc:
            return self._failure("provider_error", screen_binding, started, message=type(exc).__name__)
        valid_marks = [candidate for candidate in all_candidates if candidate.valid]
        if len(valid_marks) == 0:
            return self._failure(
                "grounding_no_candidate", screen_binding, started, message="no valid bbox", candidates=all_candidates
            )
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=provider_input_hash,
            latency_ms=self._latency_ms(started),
            marks=valid_marks,
            candidates=all_candidates,
            candidate_count=len(all_candidates),
            status="success",
            hints=[hint.redacted_summary() for hint in hints or []],
            metadata={"model_path": str(self.model_path)},
        )

    def _prepare_image(self, screenshot: Any) -> tuple[Image.Image, str]:
        raw = base64.b64decode(getattr(screenshot, "base64_data", ""))
        image = Image.open(BytesIO(raw)).convert("RGB")
        image.thumbnail((self.max_size, self.max_size))
        buffered = BytesIO()
        image.save(buffered, format="PNG", optimize=True)
        provider_bytes = buffered.getvalue()
        return Image.open(BytesIO(provider_bytes)).convert("RGB"), hashlib.sha256(provider_bytes).hexdigest()[:16]

    def _run_model(self, image: Image.Image, description: str, *, timeout: float | None = None) -> str:
        # Lazy import keeps default CI and non-MLX installs independent of the optional extra.
        from mlx_vlm import generate, load  # type: ignore

        if self._model is None or self._processor is None:
            self._model, self._processor = load(str(self.model_path))
        prompt = self._build_prompt(description)

        pbd_generate = getattr(self._model, "pbd_generate", None)
        if callable(pbd_generate):
            return self._run_pbd_generate(image, prompt)

        result = generate(
            self._model,
            self._processor,
            prompt=prompt,
            image=image,
            max_tokens=2048,
            temperature=0.0,
            generation_mode="hybrid",
        )
        return str(getattr(result, "text", result))

    def _run_pbd_generate(self, image: Image.Image, prompt: str) -> str:
        """Run LocateAnything through its model-specific Parallel Box Decoding path."""
        from mlx_vlm.utils import prepare_inputs  # type: ignore

        inputs = prepare_inputs(
            self._processor,
            images=[image],
            prompts=prompt,
        )
        input_ids = inputs.pop("input_ids")
        inputs.pop("attention_mask", None)
        tokens = self._model.pbd_generate(
            input_ids,
            generation_mode="hybrid",
            max_tokens=2048,
            **inputs,
        )
        return str(self._processor.decode(tokens, skip_special_tokens=False))

    def _build_prompt(self, description: str) -> str:
        instruction = f"Locate the region that matches the following description: {description}."
        try:
            from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore

            return str(
                apply_chat_template(
                    self._processor,
                    getattr(self._model, "config", {"model_type": "locateanything"}),
                    instruction,
                    num_images=1,
                )
            )
        except Exception:
            # LocateAnything processors expand any <image-N> placeholder in order.
            # Keep a conservative fallback for older mlx-vlm branches without prompt_utils.
            return f"<image-0>{instruction}"

    def _failure(
        self,
        code: str,
        screen_binding: ScreenBinding,
        started: float,
        *,
        message: str | None = None,
        candidates: list[MarkCandidate] | None = None,
    ) -> MarkProviderResult:
        return MarkProviderResult(
            success=False,
            provider=self.name,
            failure_code=code,
            message=message or code,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            latency_ms=self._latency_ms(started),
            marks=[],
            candidates=candidates or [],
            candidate_count=len(candidates or []),
            status=code,
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
