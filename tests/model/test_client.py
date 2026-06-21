import json
import importlib.util
from pathlib import Path

import pytest

from phone_agent.config import get_system_prompt
from phone_agent.model.client import ModelClient, ModelConfig, ModelParseError

MAIN_PATH = Path(__file__).resolve().parents[2] / "main.py"
MAIN_SPEC = importlib.util.spec_from_file_location("phone_agent_cli_main", MAIN_PATH)
assert MAIN_SPEC is not None and MAIN_SPEC.loader is not None
main = importlib.util.module_from_spec(MAIN_SPEC)
MAIN_SPEC.loader.exec_module(main)


def test_model_config_rejects_invalid_output_mode() -> None:
    with pytest.raises(ValueError, match="output_mode"):
        ModelConfig(output_mode="bad")  # type: ignore[arg-type]


def test_model_config_defaults_to_structured_json_schema() -> None:
    assert ModelConfig().output_mode == "json_schema"


def test_model_config_rejects_invalid_http_options() -> None:
    with pytest.raises(ValueError, match="timeout"):
        ModelConfig(timeout=0)
    with pytest.raises(ValueError, match="max_retries"):
        ModelConfig(max_retries=-1)


def test_model_config_rejects_invalid_thinking_options() -> None:
    with pytest.raises(ValueError, match="thinking_mode"):
        ModelConfig(thinking_mode="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="thinking_param"):
        ModelConfig(thinking_param="bad")  # type: ignore[arg-type]


def test_build_extra_body_applies_enable_thinking_flag() -> None:
    client = ModelClient(
        ModelConfig(
            extra_body={"top_k": 20},
            thinking_mode="off",
            thinking_param="enable_thinking",
        )
    )

    assert client._build_extra_body() == {"top_k": 20, "enable_thinking": False}


def test_build_extra_body_applies_chat_template_kwargs() -> None:
    client = ModelClient(
        ModelConfig(
            extra_body={"chat_template_kwargs": {"foo": "bar"}},
            thinking_mode="on",
            thinking_param="chat_template_kwargs",
        )
    )

    assert client._build_extra_body() == {
        "chat_template_kwargs": {"foo": "bar", "enable_thinking": True}
    }


def test_model_client_passes_http_options_to_openai(monkeypatch) -> None:
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("phone_agent.model.client.OpenAI", FakeOpenAI)

    ModelClient(
        ModelConfig(
            base_url="https://relay.example/v1",
            api_key="sk-test",
            timeout=12.5,
            max_retries=4,
            default_headers={"User-Agent": "Open-AutoGLM/1"},
        )
    )

    assert captured["base_url"] == "https://relay.example/v1"
    assert captured["api_key"] == "sk-test"
    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 4
    assert captured["default_headers"] == {"User-Agent": "Open-AutoGLM/1"}


def test_parse_env_headers_accepts_json_and_key_value() -> None:
    assert main.parse_env_headers('{"X-Relay":"ok"}') == {"X-Relay": "ok"}
    assert main.parse_env_headers("X-Relay=ok,User-Agent: Test") == {
        "X-Relay": "ok",
        "User-Agent": "Test",
    }


def test_parse_json_object_accepts_only_objects() -> None:
    assert main.parse_json_object('{"enable_thinking":false}', "test") == {
        "enable_thinking": False
    }
    with pytest.raises(ValueError, match="JSON object"):
        main.parse_json_object("[]", "test")


def test_main_build_model_extra_body_applies_thinking_mode() -> None:
    assert main.build_model_extra_body({"top_k": 20}, "off", "enable_thinking") == {
        "top_k": 20,
        "enable_thinking": False,
    }


def test_parse_env_headers_rejects_unsafe_headers() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        main.parse_env_headers("Host=evil.example")
    with pytest.raises(ValueError, match="control characters"):
        main.parse_env_headers('{"X-Test":"bad\\nvalue"}')


def test_build_model_headers_adds_cloudflare_access(monkeypatch) -> None:
    class Args:
        user_agent = "Open-AutoGLM/1"

    monkeypatch.setenv("PHONE_AGENT_HTTP_HEADERS", '{"X-Relay":"ok"}')
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID", "id")
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET", "secret")

    headers = main.build_model_headers(Args())

    assert headers["X-Relay"] == "ok"
    assert headers["User-Agent"] == "Open-AutoGLM/1"
    assert headers["CF-Access-Client-Id"] == "id"
    assert headers["CF-Access-Client-Secret"] == "secret"


def test_build_model_headers_rejects_partial_cloudflare_access(monkeypatch) -> None:
    class Args:
        user_agent = None

    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID", "id")
    monkeypatch.delenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="configured together"):
        main.build_model_headers(Args())


def test_build_model_headers_validates_user_agent(monkeypatch) -> None:
    class Args:
        user_agent = "bad\nagent"

    with pytest.raises(ValueError, match="control characters"):
        main.build_model_headers(Args())


def test_build_model_headers_uses_browser_compatible_default_user_agent(monkeypatch) -> None:
    class Args:
        user_agent = None

    monkeypatch.delenv("PHONE_AGENT_HTTP_HEADERS", raising=False)
    monkeypatch.delenv("PHONE_AGENT_USER_AGENT", raising=False)

    headers = main.build_model_headers(Args())

    assert "Mozilla/5.0" in headers["User-Agent"]
    assert "Open-AutoGLM" in headers["User-Agent"]


def test_diagnose_model_api_passes_runtime_http_options(monkeypatch) -> None:
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            if kwargs.get("stream"):
                delta = type("Delta", (), {"content": "ok", "reasoning_content": None})()
                choice = type("Choice", (), {"delta": delta})()
                return [type("Chunk", (), {"choices": [choice]})()]
            return type("Response", (), {"choices": [object()]})()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = type(
                "Chat", (), {"completions": FakeCompletions()}
            )()

    monkeypatch.setattr(main, "OpenAI", FakeOpenAI)

    assert main.diagnose_model_api(
        "https://relay.example/v1",
        "model",
        "sk-test",
        headers={"User-Agent": "Test"},
        timeout=7.5,
        max_retries=0,
        stream=True,
        extra_body={"enable_thinking": False},
    )

    assert captured["client"]["base_url"] == "https://relay.example/v1"
    assert captured["client"]["api_key"] == "sk-test"
    assert captured["client"]["timeout"] == 7.5
    assert captured["client"]["max_retries"] == 0
    assert captured["client"]["default_headers"] == {"User-Agent": "Test"}
    assert captured["request"]["model"] == "model"
    assert captured["request"]["stream"] is True
    assert captured["request"]["extra_body"] == {"enable_thinking": False}


def test_diagnose_model_api_redacts_sensitive_error(monkeypatch, capsys) -> None:
    class FakeOpenAI:
        def __init__(self, **kwargs):
            raise RuntimeError("bad sk-cli secret-value token-value relay-secret")

    monkeypatch.setattr(main, "OpenAI", FakeOpenAI)

    assert not main.diagnose_model_api(
        "https://relay.example/v1",
        "model",
        api_key="sk-cli",
        headers={
            "X-Api-Key": "secret-value",
            "X-Auth-Token": "token-value",
            "X-Relay": "relay-secret",
        },
    )

    output = capsys.readouterr().out
    assert "sk-cli" not in output
    assert "secret-value" not in output
    assert "token-value" not in output
    assert "relay-secret" not in output
    assert "[REDACTED]" in output


def test_parse_response_prefers_xml_answer_over_inner_json() -> None:
    client = ModelClient()

    thinking, action = client._parse_response(
        '<think>思考</think><answer>{"type":"do","action":"wait","duration":"1 seconds"}</answer>'
    )

    assert thinking == "思考"
    assert action == '{"type":"do","action":"wait","duration":"1 seconds"}'
    assert "</answer>" not in action


@pytest.mark.parametrize(
    ("content", "expected_action"),
    (
        ('  {"type":"do","action":"wait","duration":"1 seconds"}  ', '{"type":"do","action":"wait","duration":"1 seconds"}'),
        ('```\n{"type":"finish","message":"done"}\n```', '{"type":"finish","message":"done"}'),
        ('```json\n{"type":"do","action":"back"}\n```', '{"type":"do","action":"back"}'),
    ),
)
def test_parse_response_normalizes_structured_text_and_code_fence(
    content: str, expected_action: str
) -> None:
    client = ModelClient()

    thinking, action = client._parse_response(content)

    assert thinking == ""
    assert action == expected_action


@pytest.mark.parametrize(
    "content",
    (
        "",
        "   ",
        '<think>bad</think><answer>{"type":"do","action":"wait"}',
        '</answer>{"type":"do","action":"wait"}',
        '<think>bad</think><answer>   </answer>',
    ),
)
def test_parse_response_rejects_empty_or_malformed_xml(content: str) -> None:
    client = ModelClient()

    with pytest.raises(ValueError):
        client._parse_response(content)


def test_parse_response_with_metadata_adapts_json_schema() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    thinking, action, metadata = client._parse_response_with_metadata(
        '{"type":"intent","action":"tap","target_mark_id":"m1"}'
    )

    assert thinking == ""
    assert '"_metadata": "intent"' in action
    assert '"action": "Tap"' in action
    assert '"target_mark_id": "m1"' in action
    assert metadata["configured_mode"] == "json_schema"
    assert metadata["detected_format"] == "json_schema"
    assert metadata["adapter_used"] == "json_schema"
    assert metadata["parse_success"] is True


def test_parse_response_with_metadata_accepts_expected_outcome_envelope() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    _thinking, action, metadata = client._parse_response_with_metadata(
        json.dumps(
            {
                "action": {"type": "intent", "action": "tap", "target_mark_id": "m1"},
                "expected_outcome": {
                    "kind": "input_focused",
                    "must_observe": ["搜索", "取消"],
                },
            },
            ensure_ascii=False,
        )
    )

    assert '"expected_outcome"' in action
    assert '"target_mark_id": "m1"' in action
    assert metadata["parse_success"] is True
    assert metadata["expected_outcome_present"] is True


def test_parse_response_with_metadata_adapts_tool_calls() -> None:
    client = ModelClient(ModelConfig(output_mode="tool_calls"))

    _thinking, action, metadata = client._parse_response_with_metadata(
        "",
        tool_calls=[
            {
                "function": {
                    "name": "do",
                    "arguments": '{"type":"do","action":"back"}',
                }
            }
        ],
    )

    assert '"action": "Back"' in action
    assert metadata["detected_format"] == "tool_calls"
    assert metadata["adapter_used"] == "tool_calls"


def test_model_config_rejects_removed_text_dsl_mode() -> None:
    with pytest.raises(ValueError, match="output_mode"):
        ModelConfig(output_mode="text_dsl")  # type: ignore[arg-type]


def test_json_schema_mode_rejects_text_dsl_response() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("legacy text action")

    assert exc_info.value.parse_metadata["parse_error_code"] == "invalid_json"
    assert "raw_model_response" not in exc_info.value.parse_metadata


def test_trace_raw_model_response_opt_in_records_parse_failure_text() -> None:
    client = ModelClient(
        ModelConfig(output_mode="json_schema", trace_raw_model_response=True)
    )

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("legacy text action")

    metadata = exc_info.value.parse_metadata
    assert metadata["parse_error_code"] == "invalid_json"
    assert metadata["raw_model_response"] == "legacy text action"
    assert metadata["raw_model_response_length"] == len("legacy text action")


def test_json_schema_mode_rejects_direct_coordinate_tap() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata('{"type":"do","action":"tap","x":1,"y":2}')

    assert exc_info.value.parse_metadata["parse_error_code"] == "mark_required"


def test_tool_calls_mode_rejects_plain_text_without_tool_call() -> None:
    client = ModelClient(ModelConfig(output_mode="tool_calls"))

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("legacy text action")

    assert exc_info.value.parse_metadata["parse_error_code"] == "unsupported_tool_call"


def test_auto_mode_detects_json_and_rejects_legacy_text_dsl() -> None:
    client = ModelClient(ModelConfig(output_mode="auto"))

    _thinking, json_action, json_metadata = client._parse_response_with_metadata(
        '{"type":"intent","action":"tap","target_mark_id":"m1"}'
    )

    assert '"action": "Tap"' in json_action
    assert json_metadata["detected_format"] == "json_schema"
    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("legacy text action")
    assert exc_info.value.parse_metadata["parse_error_code"] == "invalid_json"


def test_parse_response_with_metadata_rejects_unsupported_text_action() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("not an action")

    assert exc_info.value.parse_metadata["parse_success"] is False
    assert exc_info.value.parse_metadata["parse_error_code"] == "invalid_json"


def test_parse_response_with_metadata_rejects_unsafe_text_dsl_metadata() -> None:
    client = ModelClient(ModelConfig(output_mode="auto"))

    with pytest.raises(ModelParseError) as exc_info:
        client._parse_response_with_metadata("legacy text action with unsafe payload")

    assert exc_info.value.parse_metadata["parse_success"] is False
    assert exc_info.value.parse_metadata["parse_error_code"] == "invalid_json"


def test_tool_specs_include_swipe_start_end_fields() -> None:
    client = ModelClient(ModelConfig(output_mode="tool_calls"))

    do_tool = client._build_tool_specs()[0]
    properties = do_tool["function"]["parameters"]["properties"]

    assert "start" in properties
    assert "end" in properties


def test_tool_call_delta_aggregation() -> None:
    class FunctionDelta:
        def __init__(self, name=None, arguments=None):
            self.name = name
            self.arguments = arguments

    class ToolCallDelta:
        def __init__(self, index, function):
            self.index = index
            self.function = function
            self.id = None
            self.type = "function"

    client = ModelClient(ModelConfig(output_mode="tool_calls"))
    aggregated = {}

    client._accumulate_tool_call_deltas(
        aggregated,
        [ToolCallDelta(0, FunctionDelta(name="do", arguments='{"type":"do",'))],
    )
    client._accumulate_tool_call_deltas(
        aggregated,
        [ToolCallDelta(0, FunctionDelta(arguments='"action":"tap","x":1,"y":2}'))],
    )

    assert aggregated[0]["function"]["name"] == "do"
    assert aggregated[0]["function"]["arguments"] == '{"type":"do","action":"tap","x":1,"y":2}'


def test_stream_consumer_handles_reasoning_content_without_polluting_action(capsys) -> None:
    class Delta:
        def __init__(self, content=None, reasoning_content=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = None

    class Choice:
        def __init__(self, delta):
            self.delta = delta

    class Chunk:
        def __init__(self, delta):
            self.choices = [Choice(delta)]

    client = ModelClient(ModelConfig(stream=True))

    raw_content, tool_calls, first_token, thinking_end = client._consume_stream(
        [
            Chunk(Delta(reasoning_content="先思考")),
            Chunk(Delta(content='<answer>{"type":"do","action":"home"}</answer>')),
        ],
        0,
    )

    assert raw_content == '<answer>{"type":"do","action":"home"}</answer>'
    assert tool_calls == {}
    assert first_token is not None
    assert thinking_end is not None
    assert "先思考" not in capsys.readouterr().out


def test_stream_consumer_can_opt_in_to_stdout(capsys) -> None:
    class Delta:
        def __init__(self, content=None, reasoning_content=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = None

    class Choice:
        def __init__(self, delta):
            self.delta = delta

    class Chunk:
        def __init__(self, delta):
            self.choices = [Choice(delta)]

    client = ModelClient(ModelConfig(stream=True, stream_stdout=True))

    client._consume_stream(
        [
            Chunk(Delta(reasoning_content="先思考")),
            Chunk(Delta(content='<answer>{"type":"do","action":"home"}</answer>')),
        ],
        0,
    )

    assert "先思考" in capsys.readouterr().out


@pytest.mark.parametrize("lang", ("cn", "en"))
@pytest.mark.parametrize("output_mode", ("json_schema", "tool_calls", "auto"))
def test_system_prompt_renders_single_output_contract(lang: str, output_mode: str) -> None:
    prompt = get_system_prompt(lang=lang, output_mode=output_mode)

    assert "Action Schema" in prompt
    assert "0-1000" in prompt
    if output_mode == "json_schema":
        assert "<answer>" not in prompt
        assert "JSON" in prompt
    elif output_mode == "tool_calls":
        assert "<answer>" not in prompt
        assert "tool" in prompt.lower()


def test_system_prompt_uses_call_api_message_contract() -> None:
    prompt = get_system_prompt(lang="cn", output_mode="json_schema")

    assert '"action":"call_api"' in prompt or '"action": "call_api"' in prompt
    assert 'Call_API", instruction=' not in prompt


def test_legacy_prompt_version_is_removed() -> None:
    with pytest.raises(ValueError, match="unsupported prompt_version"):
        get_system_prompt(
            lang="en",
            output_mode="json_schema",
            prompt_version="legacy_text_dsl",
        )


def test_tool_spec_has_action_enum() -> None:
    client = ModelClient(ModelConfig())
    specs = client._build_tool_specs()

    do_spec = specs[0]
    action_prop = do_spec["function"]["parameters"]["properties"]["action"]
    assert "enum" in action_prop
    assert "Tap" in action_prop["enum"]
    assert "Launch" in action_prop["enum"]
    assert "Take_over" in action_prop["enum"]


def test_tool_spec_app_has_description() -> None:
    client = ModelClient(ModelConfig())
    specs = client._build_tool_specs()

    app_prop = specs[0]["function"]["parameters"]["properties"]["app"]
    assert "description" in app_prop
    assert "available apps" in app_prop["description"].lower() or "system prompt" in app_prop["description"].lower()


def test_tool_spec_target_mark_has_grounding_description() -> None:
    client = ModelClient(ModelConfig())
    specs = client._build_tool_specs()

    properties = specs[0]["function"]["parameters"]["properties"]
    mark_prop = properties["target_mark_id"]
    assert "element" not in properties
    assert "x" not in properties
    assert "y" not in properties
    assert "description" in mark_prop
    assert "harness grounds" in mark_prop["description"]
    assert "target_intent" not in properties
    assert "non-executable" in properties["target_text_hint"]["description"].lower()


def test_tool_spec_duration_has_format_description() -> None:
    client = ModelClient(ModelConfig())
    specs = client._build_tool_specs()

    duration_prop = specs[0]["function"]["parameters"]["properties"]["duration"]
    assert "description" in duration_prop
    assert "60" in duration_prop["description"]
