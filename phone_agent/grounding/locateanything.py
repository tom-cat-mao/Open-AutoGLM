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
LOCATEANYTHING_STRUCTURE_MODES = {"off", "target", "screen"}
DEFAULT_VISUAL_CATEGORIES = ("button", "text", "input", "card", "image")

# D3: la_* marks carry a fixed neutral source label instead of echoing the
# model's query description back into the marks block. Echoing the query made
# la_* marks look like confirmed screen content ("model sees its own words"),
# so the neutral label keeps marks_block honest without leaking the hint.
LOCATEANYTHING_NEUTRAL_MARK_LABEL = "visual-match"


class LocateAnythingMLXProvider:
    """Lazy MLX wrapper for LocateAnything hint-to-mark generation."""

    name = "locateanything_mlx"
    version = "3b-4bit"
    allow_raw_hints = True

    def __init__(
        self,
        model_path: str | Path = "models/LocateAnything-3B-4bit",
        *,
        max_size: int = DEFAULT_LOCATEANYTHING_MAX_SIZE,
        context_max_chars: int = 0,
        structure_mode: str = "off",
        max_visual_candidates: int = 30,
        visual_category_budget: int = 5,
        max_structure_calls: int = 5,
        invalid_structure_mode: str | None = None,
    ) -> None:
        if max_size <= 0:
            raise ValueError("LocateAnything max_size must be positive")
        if context_max_chars < 0:
            raise ValueError("LocateAnything context_max_chars must be non-negative")
        if structure_mode not in LOCATEANYTHING_STRUCTURE_MODES:
            raise ValueError("LocateAnything structure_mode must be one of: off, target, screen")
        if max_visual_candidates <= 0:
            raise ValueError("LocateAnything max_visual_candidates must be positive")
        if visual_category_budget <= 0:
            raise ValueError("LocateAnything visual_category_budget must be positive")
        if max_structure_calls <= 0:
            raise ValueError("LocateAnything max_structure_calls must be positive")
        self.model_path = Path(model_path)
        self.max_size = max_size
        self.context_max_chars = context_max_chars
        self.structure_mode = structure_mode
        self.max_visual_candidates = max_visual_candidates
        self.visual_category_budget = visual_category_budget
        self.max_structure_calls = max_structure_calls
        self.invalid_structure_mode = invalid_structure_mode
        self._model = None
        self._processor = None

    def unload(self) -> None:
        """Release the loaded MLX model and clear the MLX cache (RAM fix).

        Lazy loading stays untouched: the next ``provide_marks`` reloads the
        model from disk, so a singleton provider may be unloaded between runs
        and reused afterwards. The ``mlx`` import is deferred so non-MLX
        installs are unaffected (same lazy-import pattern as ``_run_model``).
        """

        self._model = None
        self._processor = None
        try:
            import mlx.core as mx  # type: ignore

            mx.clear_cache()
        except Exception:
            pass
        import gc

        gc.collect()

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
        visual_structure_candidates: list[MarkCandidate] = []
        ambiguous_for_execution = False
        try:
            image, provider_input_hash = self._prepare_image(screenshot)
            hint_items = hints or []
            for hint_index, description in enumerate(descriptions, start=1):
                context = self._context_for_hint(hint_items[hint_index - 1] if len(hint_items) >= hint_index else None)
                if context:
                    output = self._run_model(image, description, timeout=timeout, context=context)
                else:
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
                        text_summary=LOCATEANYTHING_NEUTRAL_MARK_LABEL,
                    )
                    for index, box in enumerate(parsed_set.candidates, start=1)
                ]
                if len(valid_candidates) > 1:
                    if self.structure_mode == "off":
                        return self._failure(
                            "grounding_ambiguous",
                            screen_binding,
                            started,
                            message="multiple valid bboxes",
                            candidates=all_candidates + candidates,
                        )
                    ambiguous_for_execution = True
                    visual_structure_candidates.extend([candidate for candidate in candidates if candidate.valid])
                all_candidates.extend(candidates)
            if self.structure_mode == "screen":
                visual_structure_candidates.extend(
                    self._screen_structure_candidates(image, timeout=timeout)[: self.max_visual_candidates]
                )
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
        executable_marks = valid_marks if self.structure_mode == "off" else ([] if ambiguous_for_execution else valid_marks[:1])
        visual_candidates = (visual_structure_candidates or valid_marks)[: self.max_visual_candidates]
        screen_structures = []
        if self.structure_mode != "off":
            screen_structures = [
                self._build_visual_structure(
                    screen_binding,
                    visual_candidates,
                    provider_input_hash=provider_input_hash,
                )
            ]
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=provider_input_hash,
            latency_ms=self._latency_ms(started),
            marks=executable_marks,
            candidates=all_candidates + [candidate for candidate in visual_candidates if candidate not in all_candidates],
            candidate_count=len(all_candidates),
            status="success",
            hints=[hint.redacted_summary() for hint in hints or []],
            metadata={
                "model_path": str(self.model_path),
                "context_max_chars": self.context_max_chars,
                "structure_mode": self.structure_mode,
                "invalid_structure_mode": self.invalid_structure_mode,
                "visual_structure_candidate_count": len(visual_candidates),
                "executable_mark_count": len(executable_marks),
                "non_executable_candidate_count": max(0, len(visual_candidates) - len(executable_marks)),
                "ambiguous_for_execution": ambiguous_for_execution,
                "visual_structure_failure_codes": getattr(self, "_last_screen_structure_failures", []),
                "visual_structure_partial": bool(getattr(self, "_last_screen_structure_failures", [])),
            },
            screen_structures=screen_structures,
        )

    def _screen_structure_candidates(self, image: Image.Image, *, timeout: float | None = None) -> list[MarkCandidate]:
        candidates: list[MarkCandidate] = []
        self._last_screen_structure_failures: list[str] = []
        categories = DEFAULT_VISUAL_CATEGORIES[: self.visual_category_budget]
        for call_index, category in enumerate(categories[: self.max_structure_calls], start=1):
            try:
                output = self._run_model(image, f"all visible {category} UI elements", timeout=timeout)
                parsed_set = parse_box_candidates(output)
            except GroundingParseError as exc:
                self._last_screen_structure_failures.append(exc.code)
                continue
            for index, box in enumerate(parsed_set.valid_candidates, start=1):
                candidates.append(
                    MarkCandidate(
                        mark_id=f"la_screen_{call_index}_{index}",
                        bbox=box.bbox,
                        center=box.center,
                        confidence=None,
                        source=self.name,
                        role=category,
                        text_summary=category,
                    )
                )
                if len(candidates) >= self.max_visual_candidates:
                    return candidates
        return candidates

    def _build_visual_structure(
        self,
        screen_binding: ScreenBinding,
        candidates: list[MarkCandidate],
        *,
        provider_input_hash: str | None,
    ) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates[: self.max_visual_candidates], start=1):
            role = str(candidate.role or "visual_target")
            nodes[f"visual_{index}"] = {
                "node_id": f"visual_{index}",
                "path": f"visual/{index}",
                "parent_id": None,
                "child_ids": [],
                "depth": 0,
                "bounds": list(candidate.bbox),
                "role": role,
                "text_summary": _safe_visual_text(candidate.text_summary, role),
                "visible": True,
                "structure_kind": "visual",
                "source_provider": self.name,
                "confidence_tier": "weak",
                "node_provenance": "locateanything_box",
                "visual_order": index,
                "confidence": candidate.confidence,
            }
        digest_source = "|".join(f"{key}:{item.get('bounds')}:{item.get('role')}" for key, item in nodes.items())
        structure_digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
        return {
            "screen_id": screen_binding.screen_id,
            "semantic_screen_id": screen_binding.semantic_screen_id,
            "mark_set_version": screen_binding.mark_set_version,
            "status": "ok" if nodes else "visual_structure_partial",
            "structure_kind": "visual",
            "source_provider": self.name,
            "confidence_tier": "weak",
            "structure_version": "locateanything_visual_v1",
            "structure_digest": structure_digest,
            "topology_digest": structure_digest,
            "node_count": len(nodes),
            "root_node_id": None,
            "nodes": nodes,
            "metadata": {
                "provider_input_hash": provider_input_hash,
                "structure_mode": self.structure_mode,
                "candidate_count": len(candidates),
            },
        }

    def _prepare_image(self, screenshot: Any) -> tuple[Image.Image, str]:
        raw = base64.b64decode(getattr(screenshot, "base64_data", ""))
        image = Image.open(BytesIO(raw)).convert("RGB")
        image.thumbnail((self.max_size, self.max_size))
        buffered = BytesIO()
        image.save(buffered, format="PNG", optimize=True)
        provider_bytes = buffered.getvalue()
        return Image.open(BytesIO(provider_bytes)).convert("RGB"), hashlib.sha256(provider_bytes).hexdigest()[:16]

    def _run_model(
        self,
        image: Image.Image,
        description: str,
        *,
        timeout: float | None = None,
        context: str | None = None,
    ) -> str:
        # Lazy import keeps default CI and non-MLX installs independent of the optional extra.
        from mlx_vlm import generate, load  # type: ignore

        if self._model is None or self._processor is None:
            self._model, self._processor = load(str(self.model_path))
        prompt = self._build_prompt(description, context=context)

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

    def _build_prompt(self, description: str, *, context: str | None = None) -> str:
        instruction = self._build_instruction(description, context=context)
        try:
            from mlx_vlm.prompt_utils import apply_chat_template  # type: ignore
        except ImportError:
            # Older mlx-vlm builds may not expose prompt_utils. Keep the legacy
            # image placeholder fallback only for that explicit compatibility case.
            return f"<image-0>{instruction}"
        return str(
            apply_chat_template(
                self._processor,
                getattr(self._model, "config", {"model_type": "locateanything"}),
                instruction,
                num_images=1,
            )
        )

    def _build_instruction(self, description: str, *, context: str | None = None) -> str:
        instruction = f"Locate the region that matches the following description: {description}."
        bounded_context = self._bound_context(context)
        if bounded_context:
            return f"{instruction}\nContext: {bounded_context}"
        return instruction

    def _context_for_hint(self, hint: MarkProviderHint | None) -> str | None:
        if hint is None or self.context_max_chars <= 0:
            return None
        pieces = [hint.role, hint.intent, hint.action]
        context = " ".join(str(piece).strip() for piece in pieces if str(piece or "").strip())
        return self._bound_context(context)

    def _bound_context(self, context: str | None) -> str:
        if self.context_max_chars <= 0 or not context:
            return ""
        return " ".join(str(context).split())[: self.context_max_chars]

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
            metadata={
                "structure_mode": self.structure_mode,
                "invalid_structure_mode": self.invalid_structure_mode,
            },
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


def _safe_visual_text(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    safe_terms = {
        "button",
        "text",
        "input",
        "card",
        "image",
        "search",
        "搜索",
        "visual_target",
        LOCATEANYTHING_NEUTRAL_MARK_LABEL,
    }
    if text.casefold() in safe_terms:
        return text
    return fallback if fallback.casefold() in safe_terms else "visual_target"
