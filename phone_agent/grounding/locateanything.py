"""Optional LocateAnything-3B-4bit MLX grounding provider."""

from __future__ import annotations

import base64
import hashlib
import platform
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from phone_agent.grounding.parser import GroundingParseError, parse_box_response
from phone_agent.grounding.provider import GroundingResult, GroundingTarget, ScreenBinding


class LocateAnythingMLXProvider:
    """Lazy MLX wrapper for LocateAnything target-to-bbox grounding."""

    name = "locateanything_mlx"
    version = "3b-4bit"

    def __init__(self, model_path: str | Path = "models/LocateAnything-3B-4bit", *, max_size: int = 1280) -> None:
        self.model_path = Path(model_path)
        self.max_size = max_size
        self._model = None
        self._processor = None

    def ground(
        self,
        screenshot: Any,
        target: GroundingTarget,
        screen_binding: ScreenBinding,
        timeout: float | None = None,
    ) -> GroundingResult:
        started = time.perf_counter()
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return self._failure("unsupported_platform", screen_binding, started)
        if not self.model_path.exists():
            return self._failure("model_not_found", screen_binding, started)
        description = target.description()
        if not description:
            return self._failure("missing_target", screen_binding, started)
        try:
            image, provider_input_hash = self._prepare_image(screenshot)
            output = self._run_model(image, description, timeout=timeout)
            parsed = parse_box_response(output)
        except GroundingParseError as exc:
            return self._failure(exc.code, screen_binding, started, message=str(exc))
        except ImportError:
            return self._failure("import_error", screen_binding, started)
        except TimeoutError:
            return self._failure("timeout", screen_binding, started)
        except Exception as exc:
            return self._failure("provider_error", screen_binding, started, message=type(exc).__name__)
        return GroundingResult(
            success=True,
            provider=self.name,
            bbox=parsed.bbox,
            center=parsed.center,
            confidence=None,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=provider_input_hash,
            latency_ms=self._latency_ms(started),
            metadata={"model_path": str(self.model_path), "target": target.redacted_summary()},
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
        prompt = (
            f"Find the UI element: {description}. "
            "Return its bounding box as <box><x1><y1><x2><y2></box>"
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            image.save(tmp.name, format="PNG")
            return str(generate(self._model, self._processor, prompt=prompt, image=tmp.name, verbose=False))

    def _failure(
        self,
        code: str,
        screen_binding: ScreenBinding,
        started: float,
        *,
        message: str | None = None,
    ) -> GroundingResult:
        return GroundingResult(
            success=False,
            provider=self.name,
            failure_code=code,
            message=message or code,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            latency_ms=self._latency_ms(started),
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
