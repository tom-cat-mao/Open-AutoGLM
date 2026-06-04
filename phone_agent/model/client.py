"""Model client for AI inference using OpenAI-compatible API."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from openai import OpenAI

from phone_agent.actions.adapter import ActionAdapterError, adapt_json_action, adapt_tool_calls
from phone_agent.config.i18n import get_message

OutputMode = Literal["json_schema", "tool_calls", "auto"]
VALID_OUTPUT_MODES = {"json_schema", "tool_calls", "auto"}


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
    timeout: float = 60.0
    max_retries: int = 2
    default_headers: dict[str, str] = field(default_factory=dict)
    stream: bool = False
    max_tokens: int = 3000
    temperature: float = 0.0
    top_p: float = 0.85
    frequency_penalty: float = 0.2
    extra_body: dict[str, Any] = field(default_factory=dict)
    thinking_mode: Literal["auto", "on", "off"] = "auto"
    thinking_param: Literal["enable_thinking", "chat_template_kwargs"] = "enable_thinking"
    lang: str = "cn"  # Language for UI messages: 'cn' or 'en'
    output_mode: OutputMode = "json_schema"
    stream_stdout: bool = False

    def __post_init__(self) -> None:
        """Validate runtime configuration values not enforced by type hints."""
        if self.output_mode not in VALID_OUTPUT_MODES:
            raise ValueError(
                "output_mode must be one of: json_schema, tool_calls, auto"
            )
        if self.timeout <= 0:
            raise ValueError("timeout must be a positive number")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.thinking_mode not in {"auto", "on", "off"}:
            raise ValueError("thinking_mode must be one of: auto, on, off")
        if self.thinking_param not in {"enable_thinking", "chat_template_kwargs"}:
            raise ValueError(
                "thinking_param must be one of: enable_thinking, chat_template_kwargs"
            )


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
        self.client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            default_headers=self.config.default_headers or None,
        )

    def request(
        self,
        messages: list[dict[str, Any]],
        output_mode: OutputMode | None = None,
        validate_action: bool = True,
    ) -> ModelResponse:
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

        effective_output_mode = output_mode or self.config.output_mode
        if effective_output_mode not in VALID_OUTPUT_MODES:
            raise ValueError(
                "output_mode must be one of: json_schema, tool_calls, auto"
            )

        extra_body = self._build_extra_body()
        request_kwargs: dict[str, Any] = {
            "messages": cast(Any, messages),
            "model": self.config.model_name,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "frequency_penalty": self.config.frequency_penalty,
            "extra_body": extra_body,
            "stream": self.config.stream,
        }
        if effective_output_mode == "tool_calls":
            request_kwargs["tools"] = self._build_tool_specs()
            request_kwargs["tool_choice"] = "auto"
        elif effective_output_mode == "json_schema":
            request_kwargs["response_format"] = {"type": "json_object"}

        raw_content = ""
        tool_call_deltas: dict[int, dict[str, Any]] = {}
        if self.config.stream:
            stream: Any = self.client.chat.completions.create(
                **request_kwargs,
            )
            raw_content, tool_call_deltas, time_to_first_token, time_to_thinking_end = (
                self._consume_stream(stream, start_time)
            )
        else:
            response: Any = self.client.chat.completions.create(
                **request_kwargs,
            )
            if response.choices:
                message = response.choices[0].message
                raw_content = message.content or ""
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    tool_call_deltas = {
                        index: self._tool_call_to_dict(tool_call)
                        for index, tool_call in enumerate(tool_calls)
                    }

        # Calculate total time
        total_time = time.time() - start_time

        # Parse thinking and action from response
        thinking, action, parse_metadata = self._parse_response_with_metadata(
            raw_content,
            tool_calls=list(tool_call_deltas.values()) if tool_call_deltas else None,
            output_mode=effective_output_mode,
            validate_action=validate_action,
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

    def _build_extra_body(self) -> dict[str, Any]:
        """Build provider-specific request body extensions."""
        extra_body = dict(self.config.extra_body)
        if self.config.thinking_mode == "auto":
            return extra_body

        enable_thinking = self.config.thinking_mode == "on"
        if self.config.thinking_param == "chat_template_kwargs":
            chat_template_kwargs = dict(extra_body.get("chat_template_kwargs") or {})
            chat_template_kwargs["enable_thinking"] = enable_thinking
            extra_body["chat_template_kwargs"] = chat_template_kwargs
        else:
            extra_body["enable_thinking"] = enable_thinking
        return extra_body

    def _consume_stream(
        self, stream: Any, start_time: float
    ) -> tuple[str, dict[int, dict[str, Any]], float | None, float | None]:
        """Consume a streaming OpenAI-compatible response."""
        raw_content = ""
        tool_call_deltas: dict[int, dict[str, Any]] = {}
        time_to_first_token = None
        time_to_thinking_end = None
        buffer = ""  # Buffer to hold content that might be part of a marker
        action_markers = ['"type"', '"action"', '"message"']
        in_action_phase = False  # Track if we've entered the action phase
        first_token_received = False
        saw_reasoning_content = False

        for chunk in stream:
            if len(chunk.choices) == 0:
                continue
            delta = chunk.choices[0].delta
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                self._accumulate_tool_call_deltas(tool_call_deltas, tool_calls)
                if not first_token_received:
                    time_to_first_token = time.time() - start_time
                    first_token_received = True

            reasoning_content = getattr(delta, "reasoning_content", None)
            if reasoning_content:
                saw_reasoning_content = True
                if not first_token_received:
                    time_to_first_token = time.time() - start_time
                    first_token_received = True
                if self.config.stream_stdout and not in_action_phase:
                    print(reasoning_content, end="", flush=True)

            if delta.content is not None:
                content = delta.content
                raw_content += content

                # Record time to first token
                if not first_token_received:
                    time_to_first_token = time.time() - start_time
                    first_token_received = True

                if saw_reasoning_content and time_to_thinking_end is None:
                    # Providers such as Qwen stream reasoning in `reasoning_content`
                    # and final answer in `content`. Seeing content after a reasoning
                    # phase marks the end of thinking even if the final answer has no
                    # do()/finish() marker yet.
                    time_to_thinking_end = time.time() - start_time

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
                        if self.config.stream_stdout:
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
                    if self.config.stream_stdout:
                        print(buffer, end="", flush=True)
                    buffer = ""

        return raw_content, tool_call_deltas, time_to_first_token, time_to_thinking_end

    def _parse_response_with_metadata(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        output_mode: OutputMode | None = None,
        validate_action: bool = True,
    ) -> tuple[str, str, dict[str, Any]]:
        """Parse response according to configured output mode with observability metadata."""
        effective_output_mode = output_mode or self.config.output_mode
        metadata: dict[str, Any] = {
            "provider": "openai_compatible",
            "configured_mode": effective_output_mode,
            "detected_format": "unknown",
            "adapter_used": "none",
            "parse_success": False,
            "parse_error_code": None,
        }
        try:
            if effective_output_mode == "tool_calls":
                if not tool_calls:
                    raise ActionAdapterError(
                        "unsupported_tool_call", "tool_calls mode requires a provider tool call"
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

            if tool_calls:
                if effective_output_mode != "auto":
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
            if effective_output_mode == "json_schema":
                if not self._looks_like_json(normalized):
                    raise ActionAdapterError(
                        "invalid_json", "json_schema mode requires a JSON object response"
                    )
                if not validate_action:
                    metadata.update(
                        {
                            "detected_format": "json_schema",
                            "adapter_used": "raw_json",
                            "parse_success": True,
                        }
                    )
                    return "", normalized, metadata
                action = adapt_json_action(normalized)
                metadata.update(
                    {
                        "detected_format": "json_schema",
                        "adapter_used": "json_schema",
                        "parse_success": True,
                    }
                )
                return "", json.dumps(action, ensure_ascii=False), metadata

            if effective_output_mode == "auto" and self._looks_like_json(normalized):
                if not validate_action:
                    metadata.update(
                        {
                            "detected_format": "json_schema",
                            "adapter_used": "raw_json",
                            "parse_success": True,
                        }
                    )
                    return "", normalized, metadata
                action = adapt_json_action(normalized)
                metadata.update(
                    {
                        "detected_format": "json_schema",
                        "adapter_used": "json_schema",
                        "parse_success": True,
                    }
                )
                return "", json.dumps(action, ensure_ascii=False), metadata

            raise ActionAdapterError(
                "invalid_json",
                "structured output mode requires a JSON object or provider tool call",
            )
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
        3. Otherwise, return the normalized content for internal compatibility.
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

    def _tool_call_to_dict(self, tool_call: Any) -> dict[str, Any]:
        """Convert a non-streaming OpenAI tool call object into adapter input."""
        function = getattr(tool_call, "function", None)
        return {
            "id": getattr(tool_call, "id", ""),
            "type": getattr(tool_call, "type", "function"),
            "function": {
                "name": getattr(function, "name", "") if function else "",
                "arguments": getattr(function, "arguments", "") if function else "",
            },
        }

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
                            "action": {
                                "type": "string",
                                "enum": [
                                    "Tap", "Double Tap", "Long Press", "Swipe",
                                    "Type", "Type_Name", "Back", "Home",
                                    "Launch", "Wait", "Note", "Call_API",
                                    "Interact", "Take_over",
                                ],
                            },
                            "start": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "[x, y] swipe start in 0-1000",
                            },
                            "end": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "[x, y] swipe end in 0-1000",
                            },
                            "text": {"type": "string"},
                            "message": {"type": "string"},
                            "app": {
                                "type": "string",
                                "description": "Must use a name from the available apps list in the system prompt",
                            },
                            "duration": {
                                "type": "string",
                                "description": "Format: 'N seconds', max 60 seconds",
                            },
                            "target_mark_id": {
                                "type": "string",
                                "description": "Required for tap-like screen targeting in structured modes; harness grounds it before execution",
                            },
                            "target_role": {"type": "string"},
                            "target_text_hint": {"type": "string"},
                            "target_intent": {"type": "string"},
                            "requires_grounding": {"type": "boolean"},
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
        text: str, image_base64: str | None = None, image_mime_type: str = "image/png"
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
                    "image_url": {"url": f"data:{image_mime_type};base64,{image_base64}"},
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
