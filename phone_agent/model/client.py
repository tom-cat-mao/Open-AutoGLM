"""Model client for AI inference using OpenAI-compatible API."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from openai import OpenAI

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action, adapt_tool_calls
from phone_agent.actions.handler import parse_action
from phone_agent.config.i18n import get_message

OutputMode = Literal["text_dsl", "json_schema", "tool_calls", "auto"]


class ModelParseError(ValueError):
    """Model response parse error carrying trace-safe parse metadata."""

    def __init__(self, message: str, parse_metadata: dict[str, Any]) -> None:
        super().__init__(message)
        self.parse_metadata = parse_metadata


@dataclass
class ModelConfig:
    """Configuration for the AI model."""

    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model_name: str = "autoglm-phone-9b"
    max_tokens: int = 3000
    temperature: float = 0.0
    top_p: float = 0.85
    frequency_penalty: float = 0.2
    extra_body: dict[str, Any] = field(default_factory=dict)
    lang: str = "cn"  # Language for UI messages: 'cn' or 'en'
    output_mode: OutputMode = "text_dsl"


@dataclass
class ModelResponse:
    """Response from the AI model."""

    thinking: str
    action: str
    raw_content: str
    # Performance metrics
    time_to_first_token: float | None = None  # Time to first token (seconds)
    time_to_thinking_end: float | None = None  # Time to thinking end (seconds)
    total_time: float | None = None  # Total inference time (seconds)
    parse_metadata: dict[str, Any] = field(default_factory=dict)


class ModelClient:
    """
    Client for interacting with OpenAI-compatible vision-language models.

    Args:
        config: Model configuration.
    """

    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ModelConfig()
        self.client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key)

    def request(self, messages: list[dict[str, Any]]) -> ModelResponse:
        """
        Send a request to the model.

        Args:
            messages: List of message dictionaries in OpenAI format.

        Returns:
            ModelResponse containing thinking and action.

        Raises:
            ValueError: If the response cannot be parsed.
        """
        # Start timing
        start_time = time.time()
        time_to_first_token = None
        time_to_thinking_end = None

        request_kwargs: dict[str, Any] = {
            "messages": cast(Any, messages),
            "model": self.config.model_name,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "frequency_penalty": self.config.frequency_penalty,
            "extra_body": self.config.extra_body,
            "stream": True,
        }
        if self.config.output_mode == "tool_calls":
            request_kwargs["tools"] = self._build_tool_specs()
            request_kwargs["tool_choice"] = "auto"
        elif self.config.output_mode == "json_schema":
            request_kwargs["response_format"] = {"type": "json_object"}

        stream: Any = self.client.chat.completions.create(
            **request_kwargs,
        )

        raw_content = ""
        tool_call_deltas: dict[int, dict[str, Any]] = {}
        buffer = ""  # Buffer to hold content that might be part of a marker
        action_markers = ["finish(message=", "do(action="]
        in_action_phase = False  # Track if we've entered the action phase
        first_token_received = False

        for chunk in stream:
            if len(chunk.choices) == 0:
                continue
            delta = chunk.choices[0].delta
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                self._accumulate_tool_call_deltas(tool_call_deltas, tool_calls)
            if delta.content is not None:
                content = delta.content
                raw_content += content

                # Record time to first token
                if not first_token_received:
                    time_to_first_token = time.time() - start_time
                    first_token_received = True

                if in_action_phase:
                    # Already in action phase, just accumulate content without printing
                    continue

                buffer += content

                # Check if any marker is fully present in buffer
                marker_found = False
                for marker in action_markers:
                    if marker in buffer:
                        # Marker found, print everything before it
                        thinking_part = buffer.split(marker, 1)[0]
                        print(thinking_part, end="", flush=True)
                        print()  # Print newline after thinking is complete
                        in_action_phase = True
                        marker_found = True

                        # Record time to thinking end
                        if time_to_thinking_end is None:
                            time_to_thinking_end = time.time() - start_time

                        break

                if marker_found:
                    continue  # Continue to collect remaining content

                # Check if buffer ends with a prefix of any marker
                # If so, don't print yet (wait for more content)
                is_potential_marker = False
                for marker in action_markers:
                    for i in range(1, len(marker)):
                        if buffer.endswith(marker[:i]):
                            is_potential_marker = True
                            break
                    if is_potential_marker:
                        break

                if not is_potential_marker:
                    # Safe to print the buffer
                    print(buffer, end="", flush=True)
                    buffer = ""

        # Calculate total time
        total_time = time.time() - start_time

        # Parse thinking and action from response
        thinking, action, parse_metadata = self._parse_response_with_metadata(
            raw_content,
            tool_calls=list(tool_call_deltas.values()) if tool_call_deltas else None,
        )

        # Print performance metrics
        lang = self.config.lang
        print()
        print("=" * 50)
        print(f"⏱️  {get_message('performance_metrics', lang)}:")
        print("-" * 50)
        if time_to_first_token is not None:
            print(
                f"{get_message('time_to_first_token', lang)}: {time_to_first_token:.3f}s"
            )
        if time_to_thinking_end is not None:
            print(
                f"{get_message('time_to_thinking_end', lang)}:        {time_to_thinking_end:.3f}s"
            )
        print(
            f"{get_message('total_inference_time', lang)}:          {total_time:.3f}s"
        )
        print("=" * 50)

        return ModelResponse(
            thinking=thinking,
            action=action,
            raw_content=raw_content,
            time_to_first_token=time_to_first_token,
            time_to_thinking_end=time_to_thinking_end,
            total_time=total_time,
            parse_metadata=parse_metadata,
        )

    def _parse_response_with_metadata(
        self, content: str, tool_calls: list[dict[str, Any]] | None = None
    ) -> tuple[str, str, dict[str, Any]]:
        """Parse response according to configured output mode with observability metadata."""
        metadata: dict[str, Any] = {
            "provider": "openai_compatible",
            "configured_mode": self.config.output_mode,
            "detected_format": "unknown",
            "adapter_used": "none",
            "parse_success": False,
            "parse_error_code": None,
        }
        try:
            if tool_calls:
                if self.config.output_mode not in {"tool_calls", "auto"}:
                    raise ActionAdapterError(
                        "unsupported_tool_call", "tool_calls received in non-tool_calls mode"
                    )
                action = adapt_tool_calls(tool_calls)
                metadata.update(
                    {
                        "detected_format": "tool_calls",
                        "adapter_used": "tool_calls",
                        "parse_success": True,
                    }
                )
                return "", json.dumps(action, ensure_ascii=False), metadata

            normalized = self._normalize_response_text(content)
            if self.config.output_mode in {"json_schema", "auto"} and self._looks_like_json(normalized):
                action = adapt_json_action(normalized)
                metadata.update(
                    {
                        "detected_format": "json_schema",
                        "adapter_used": "json_schema",
                        "parse_success": True,
                    }
                )
                return "", json.dumps(action, ensure_ascii=False), metadata

            thinking, action = self._parse_response(normalized)
            parse_action(action)
            metadata.update(
                {
                    "detected_format": "text_dsl",
                    "adapter_used": "text_dsl",
                    "parse_success": True,
                }
            )
            return thinking, action, metadata
        except ActionAdapterError as exc:
            metadata["parse_error_code"] = exc.code
            raise ModelParseError(f"{exc.code}: {exc}", metadata) from exc
        except ValueError as exc:
            metadata["parse_error_code"] = "parse_error"
            raise ModelParseError(str(exc), metadata) from exc

    def _parse_response(self, content: str) -> tuple[str, str]:
        """
        Parse the model response into thinking and action parts.

        Parsing rules:
        1. Strip outer Markdown code fences and surrounding whitespace.
        2. If content contains '<answer>', parse XML-style thinking/answer first.
           This prevents '</answer>' from leaking into do()/finish() actions.
        3. Otherwise, split at the earliest text DSL marker, do(...) or finish(...).
        4. Empty or malformed XML responses raise ValueError so callers can fail closed.

        Args:
            content: Raw response content.

        Returns:
            Tuple of (thinking, action).
        """
        normalized = self._normalize_response_text(content)
        if not normalized:
            raise ValueError("Empty model response")

        if "<answer>" in normalized:
            return self._parse_xml_answer(normalized)
        if "</answer>" in normalized:
            raise ValueError("Malformed XML answer: closing tag without opening tag")

        marker_positions = [
            (idx, marker)
            for marker in ("finish(message=", "do(action=")
            if (idx := normalized.find(marker)) >= 0
        ]
        if marker_positions:
            idx, marker = min(marker_positions, key=lambda item: item[0])
            thinking = self._strip_thinking_tags(normalized[:idx]).strip()
            action = (marker + normalized[idx + len(marker) :]).strip()
            return thinking, action

        return "", normalized

    def _looks_like_json(self, content: str) -> bool:
        """Return whether content looks like a JSON object."""
        return content.startswith("{") and content.endswith("}")

    def _accumulate_tool_call_deltas(
        self, aggregated: dict[int, dict[str, Any]], tool_calls: list[Any]
    ) -> None:
        """Aggregate OpenAI streaming tool_calls deltas into complete objects."""
        for fallback_index, tool_call in enumerate(tool_calls):
            index = getattr(tool_call, "index", fallback_index)
            current = aggregated.setdefault(
                index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            tool_id = getattr(tool_call, "id", None)
            if tool_id:
                current["id"] += tool_id
            tool_type = getattr(tool_call, "type", None)
            if tool_type:
                current["type"] = tool_type
            function = getattr(tool_call, "function", None)
            if function:
                name = getattr(function, "name", None)
                arguments = getattr(function, "arguments", None)
                if name:
                    current["function"]["name"] += name
                if arguments:
                    current["function"]["arguments"] += arguments

    def _build_tool_specs(self) -> list[dict[str, Any]]:
        """Build provider-facing tool specs for output formatting only."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "do",
                    "description": "Emit one phone action. This is parsed by the agent and not executed by the provider.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["do"]},
                            "action": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "element": {"type": "array", "items": {"type": "number"}},
                            "start": {"type": "array", "items": {"type": "number"}},
                            "end": {"type": "array", "items": {"type": "number"}},
                            "text": {"type": "string"},
                            "message": {"type": "string"},
                            "app": {"type": "string"},
                            "duration": {"type": ["string", "number"]},
                        },
                        "required": ["type", "action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "Finish the phone automation task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["finish"]},
                            "message": {"type": "string"},
                        },
                        "required": ["type", "message"],
                    },
                },
            },
        ]

    def _normalize_response_text(self, content: str) -> str:
        """Remove outer Markdown code fences and surrounding whitespace."""
        normalized = content.strip()
        fence_match = re.fullmatch(r"```(?:[\w+-]+)?\s*(.*?)\s*```", normalized, re.DOTALL)
        if fence_match:
            normalized = fence_match.group(1).strip()
        return normalized

    def _parse_xml_answer(self, content: str) -> tuple[str, str]:
        """Parse '<think>...</think><answer>...</answer>' style output."""
        before_answer, answer_and_tail = content.split("<answer>", 1)
        if "</answer>" not in answer_and_tail:
            raise ValueError("Malformed XML answer: missing closing tag")
        answer, _tail = answer_and_tail.split("</answer>", 1)
        action = self._normalize_response_text(answer)
        if not action:
            raise ValueError("Empty XML answer")
        thinking = self._strip_thinking_tags(before_answer).strip()
        return thinking, action

    def _strip_thinking_tags(self, content: str) -> str:
        """Remove simple thinking tags from model-visible reasoning text."""
        return content.replace("<think>", "").replace("</think>", "")


class MessageBuilder:
    """Helper class for building conversation messages."""

    @staticmethod
    def create_system_message(content: str) -> dict[str, Any]:
        """Create a system message."""
        return {"role": "system", "content": content}

    @staticmethod
    def create_user_message(
        text: str, image_base64: str | None = None
    ) -> dict[str, Any]:
        """
        Create a user message with optional image.

        Args:
            text: Text content.
            image_base64: Optional base64-encoded image.

        Returns:
            Message dictionary.
        """
        content = []

        if image_base64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                }
            )

        content.append({"type": "text", "text": text})

        return {"role": "user", "content": content}

    @staticmethod
    def create_assistant_message(content: str) -> dict[str, Any]:
        """Create an assistant message."""
        return {"role": "assistant", "content": content}

    @staticmethod
    def remove_images_from_message(message: dict[str, Any]) -> dict[str, Any]:
        """
        Remove image content from a message to save context space.

        Args:
            message: Message dictionary.

        Returns:
            Message with images removed.
        """
        if isinstance(message.get("content"), list):
            message["content"] = [
                item for item in message["content"] if item.get("type") == "text"
            ]
        return message

    @staticmethod
    def build_screen_info(current_app: str, **extra_info) -> str:
        """
        Build screen info string for the model.

        Args:
            current_app: Current app name.
            **extra_info: Additional info to include.

        Returns:
            JSON string with screen info.
        """
        info = {"current_app": current_app, **extra_info}
        return json.dumps(info, ensure_ascii=False)
