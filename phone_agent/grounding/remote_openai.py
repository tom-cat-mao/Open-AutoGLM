"""OpenAI-compatible remote grounding provider."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from io import BytesIO
from typing import Any, Callable
from urllib.parse import urlsplit

from PIL import Image

from phone_agent.graph.context import sanitize_context_payload
from phone_agent.grounding.parser import GroundingParseError, parse_box_response
from phone_agent.grounding.provider import MarkCandidate, MarkProviderHint, MarkProviderResult, ScreenBinding

DEFAULT_REMOTE_GROUNDING_BASE_URL = "https://api.stepfun.com/v1"
DEFAULT_REMOTE_GROUNDING_MODEL = "step-3.7-flash"
DEFAULT_REMOTE_GROUNDING_MAX_SIZE = 960
REMOTE_DEFAULT_CONFIDENCE = 0.5
REMOTE_GROUNDING_FAILURE_CODES = {
    "remote_missing_config",
    "remote_missing_hint",
    "remote_timeout",
    "remote_http_error",
    "remote_invalid_response",
    "remote_invalid_bbox",
    "grounding_ambiguous",
    "grounding_no_candidate",
}


class RemoteOpenAIGroundingProvider:
    """Query-conditioned mark provider backed by an OpenAI-compatible vision API."""

    name = "remote_openai"
    version = "openai-compatible-v1"
    allow_raw_hints = False

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_size: int = DEFAULT_REMOTE_GROUNDING_MAX_SIZE,
        timeout: float | None = None,
        allow_raw_hints: bool = False,
        request_callable: Callable[..., Any] | None = None,
    ) -> None:
        if max_size <= 0:
            raise ValueError("remote grounding max_size must be positive")
        self.base_url = str(base_url or DEFAULT_REMOTE_GROUNDING_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.model = str(model or DEFAULT_REMOTE_GROUNDING_MODEL)
        self.max_size = max_size
        self.timeout = timeout
        self.allow_raw_hints = bool(allow_raw_hints)
        self._request_callable = request_callable

    def provide_marks(
        self,
        screenshot: Any,
        screen_binding: ScreenBinding,
        hints: list[MarkProviderHint] | None = None,
        timeout: float | None = None,
    ) -> MarkProviderResult:
        started = time.perf_counter()
        descriptions = []
        for hint in hints or []:
            description = self._description_for_hint(hint)
            if description:
                descriptions.append(description)
        if not descriptions:
            return self._failure("remote_missing_hint", screen_binding, started)
        if self._request_callable is None and not self.api_key:
            return self._failure("remote_missing_config", screen_binding, started)

        prompt = _build_prompt(descriptions[0])
        try:
            image_data_url, provider_image_hash, image_size, image_length = self._prepare_image(screenshot)
            provider_input_hash = _provider_input_hash(provider_image_hash, prompt, self.model)
            response = self._request(prompt, image_data_url, timeout=timeout or self.timeout)
            text = _extract_response_text(response)
            parsed = parse_box_response(text)
        except TimeoutError:
            return self._failure("remote_timeout", screen_binding, started)
        except GroundingParseError as exc:
            return self._failure(_map_parse_code(exc.code), screen_binding, started, message=exc.code)
        except RemoteResponseError as exc:
            return self._failure(exc.code, screen_binding, started, message=exc.code)
        except Exception as exc:
            code = "remote_timeout" if _is_timeout_exception(exc) else "remote_http_error"
            return self._failure(code, screen_binding, started, message=type(exc).__name__)

        mark = MarkCandidate(
            mark_id="remote_1_1",
            bbox=parsed.bbox,
            center=parsed.center,
            confidence=REMOTE_DEFAULT_CONFIDENCE,
            source=self.name,
            valid=True,
            role=(hints or [None])[0].role if hints else None,
            text_summary=descriptions[0][:240],
        )
        return MarkProviderResult(
            success=True,
            provider=self.name,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            provider_input_hash=provider_input_hash,
            latency_ms=_latency_ms(started),
            marks=[mark],
            candidates=[mark],
            candidate_count=1,
            status="success",
            hints=[hint.redacted_summary() for hint in hints or []],
            metadata={
                "model": self.model,
                "base_url_host": _safe_host(self.base_url),
                "max_size": self.max_size,
                "request_image_width": image_size[0],
                "request_image_height": image_size[1],
                "request_image_length": image_length,
                "hint_length": len(descriptions[0]),
                "raw_hint_sent": self.allow_raw_hints,
                "bbox_count": 1,
                "confidence": REMOTE_DEFAULT_CONFIDENCE,
                "confidence_source": "default_conservative",
            },
        )

    def _description_for_hint(self, hint: MarkProviderHint) -> str:
        if self.allow_raw_hints:
            return hint.description()[:240]
        parts = [
            sanitize_context_payload(hint.role or "", "message", consumer="inject"),
            sanitize_context_payload(hint.text or "", "message", consumer="inject"),
            sanitize_context_payload(hint.intent or "", "message", consumer="inject"),
        ]
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())[:240]

    def _prepare_image(self, screenshot: Any) -> tuple[str, str, tuple[int, int], int]:
        raw_b64 = getattr(screenshot, "base64_data", "")
        try:
            raw = base64.b64decode(raw_b64)
            image = Image.open(BytesIO(raw)).convert("RGB")
        except Exception as exc:
            raise RemoteResponseError("remote_invalid_response") from exc
        image.thumbnail((self.max_size, self.max_size))
        buffered = BytesIO()
        image.save(buffered, format="PNG", optimize=True)
        payload = buffered.getvalue()
        digest = hashlib.sha256(payload).hexdigest()[:16]
        data_url = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
        return data_url, digest, image.size, len(payload)

    def _request(self, prompt: str, image_data_url: str, *, timeout: float | None = None) -> Any:
        if self._request_callable is not None:
            return self._request_callable(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                prompt=prompt,
                image_data_url=image_data_url,
                timeout=timeout,
            )

        from openai import OpenAI

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            temperature=0,
            timeout=timeout,
        )

    def _failure(
        self,
        code: str,
        screen_binding: ScreenBinding,
        started: float,
        *,
        message: str | None = None,
    ) -> MarkProviderResult:
        return MarkProviderResult(
            success=False,
            provider=self.name,
            failure_code=code if code in REMOTE_GROUNDING_FAILURE_CODES else "remote_invalid_response",
            message=message or code,
            screen_id=screen_binding.screen_id,
            raw_screenshot_hash=screen_binding.raw_screenshot_hash,
            latency_ms=_latency_ms(started),
            marks=[],
            candidates=[],
            candidate_count=0,
            status=code,
            metadata={
                "model": self.model,
                "base_url_host": _safe_host(self.base_url),
                "max_size": self.max_size,
                "raw_hint_sent": self.allow_raw_hints,
            },
        )


class RemoteResponseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _build_prompt(description: str) -> str:
    return (
        "You are a UI grounding model. Locate the single visible UI region that best matches the target. "
        "Return exactly one bounding box in normalized 0-1000 coordinates and no extra text. "
        "Use this exact format: <box>x1 y1 x2 y2</box>. "
        f"Target: {description}"
    )


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            return _content_to_text(content)
        content = response.get("content")
        return _content_to_text(content)
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        return _content_to_text(getattr(message, "content", None))
    content = getattr(response, "content", None)
    return _content_to_text(content)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif getattr(item, "text", None):
                parts.append(str(getattr(item, "text")))
        return "\n".join(parts)
    if content is None:
        raise RemoteResponseError("remote_invalid_response")
    return str(content)


def _provider_input_hash(image_hash: str, prompt: str, model: str) -> str:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(f"{image_hash}|{prompt_hash}|{model}".encode("utf-8")).hexdigest()[:16]


def _map_parse_code(code: str) -> str:
    if code == "grounding_ambiguous":
        return "grounding_ambiguous"
    if code in {"no_candidate", "empty_output"}:
        return "grounding_no_candidate"
    if code in {"out_of_range", "bad_order", "too_small", "too_large", "invalid_bbox"}:
        return "remote_invalid_bbox"
    return "remote_invalid_response"


def _safe_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _is_timeout_exception(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timedout" in name or "timed_out" in name


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)

