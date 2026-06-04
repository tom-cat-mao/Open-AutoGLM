from dataclasses import dataclass

import pytest

from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.reflect import parse_reflection_action, reflect_node
from phone_agent.grounding.fake import FakeGroundingProvider
from phone_agent.model.client import ModelParseError


@dataclass
class FakeModelResponse:
    thinking: str
    action: str
    parse_metadata: dict | None = None


class FakeModelClient:
    def __init__(self, response: FakeModelResponse | list[FakeModelResponse]) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.response = self.responses[-1]
        self.messages = None
        self.calls = 0

    def request(self, messages, **kwargs):
        self.messages = messages
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        self.response = response
        return response


class RaisingModelClient:
    def request(self, messages, **kwargs):
        raise RuntimeError("raw provider failure with token=secret")


def test_plan_node_returns_only_new_messages_and_resets_action_confirmed(
    base_state, fake_device
) -> None:
    base_state["messages"] = []
    base_state["step_count"] = 0
    base_state["action_confirmed"] = True
    model = FakeModelClient(
        FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "sys",
            }
        },
    )

    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "system"
    assert result["action_confirmed"] is False
    assert result["action_parsed"]["action"] == "Wait"


def test_plan_node_uses_output_mode_prompt_when_no_override(base_state, fake_device) -> None:
    base_state["messages"] = []
    base_state["step_count"] = 0
    model = FakeModelClient(FakeModelResponse("", '{"type":"finish","message":"done"}'))

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    system_prompt = model.messages[0]["content"]
    assert "JSON schema" in system_prompt
    assert "只返回一个 JSON 对象" in system_prompt


def test_plan_node_keeps_custom_system_prompt_with_output_mode(base_state, fake_device) -> None:
    base_state["messages"] = []
    base_state["step_count"] = 0
    model = FakeModelClient(FakeModelResponse("", '{"type":"finish","message":"done"}'))

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "custom sys",
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert model.messages[0]["content"] == "custom sys"


def test_plan_node_parse_failure_fails_closed_without_finish_action(
    base_state, fake_device
) -> None:
    model = FakeModelClient(FakeModelResponse("think", "not an action"))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["finished"] is True
    assert result["action_parsed"] is None
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "action_adapter_failed"
    assert result["error_layer"] == "adapter"
    assert "Model parse failed" in result["error"]
    assert model.calls == 2


def test_plan_node_parse_retry_recovers_format_only(base_state, fake_device) -> None:
    model = FakeModelClient(
        [
            FakeModelResponse("think", "not an action"),
            FakeModelResponse("", '{"type":"do","action":"wait","duration":"1 seconds"}'),
        ]
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert model.calls == 2
    assert result["action_parsed"] == {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
    assert "error" not in result


def test_plan_node_retries_real_model_parse_error(base_state, fake_device) -> None:
    class ErrorThenOkModel:
        def __init__(self) -> None:
            self.calls = 0
            self.messages = None

        def request(self, messages, **kwargs):
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                raise ModelParseError("raw invalid private text", {"parse_error_code": "invalid_json"})
            return FakeModelResponse(
                "",
                '{"_metadata":"do","action":"Wait","duration":"1 seconds"}',
                {"adapter_used": "json_schema", "parse_success": True},
            )

    model = ErrorThenOkModel()

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "output_mode": "json_schema", "verbose": False}},
    )

    assert model.calls == 2
    assert result["action_parsed"] == {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
    assert "raw invalid private text" not in str(result)


def test_plan_node_rejects_structured_coordinate_tap_without_mark_intent(
    base_state, fake_device
) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"do","action":"tap","x":500,"y":500}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "mark_required"
    assert result["finished"] is True


def test_plan_node_grounds_known_mark_intent_to_tap(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"intent","action":"tap","target_mark_id":"m1"}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "screen_marks": [
                    {"mark_id": "m1", "bbox": [100, 200, 300, 400], "role": "button", "text_summary": "张三"}
                ],
                "verbose": False,
            }
        },
    )

    assert result["intent_raw"]["target_mark_id"] == "m1"
    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [200.0, 300.0]}
    assert result["grounding_error"] is None
    text = model.messages[-1]["content"][-1]["text"]
    assert "target_mark_id" in text
    assert "张三" not in text


def test_plan_node_rejects_unknown_mark_intent(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"intent","action":"tap","target_mark_id":"missing"}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "screen_marks": [{"mark_id": "m1", "bbox": [100, 200, 300, 400]}],
                "parse_retry": 0,
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "unknown_mark"
    assert result["finished"] is True


def test_structured_coordinate_tap_requires_mark_when_marks_available(base_state, fake_device) -> None:
    model = FakeModelClient(FakeModelResponse("", '{"type":"do","action":"tap","x":200,"y":300}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "screen_marks": [{"mark_id": "m1", "bbox": [100, 200, 300, 400]}],
                "parse_retry": 0,
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "mark_required"


def test_auto_json_coordinate_tap_requires_mark_even_without_adapter_metadata(base_state, fake_device) -> None:
    model = FakeModelClient(FakeModelResponse("", '{"type":"do","action":"tap","x":200,"y":300}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "auto",
                "parse_retry": 0,
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "mark_required"
    assert result["finished"] is True


def test_plan_node_rejects_removed_text_dsl_output_mode(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"do","action":"tap","x":500,"y":500}')
    )

    with pytest.raises(ValueError, match="output_mode"):
        plan_node(
            base_state,
            {
                "configurable": {
                    "model_client": model,
                    "device_factory": fake_device,
                    "output_mode": "text_dsl",
                    "verbose": False,
                }
            },
        )


def test_plan_node_rejects_json_action_out_of_range(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"do","action":"tap","x":5000,"y":500}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["finished"] is True
    assert result["action_parsed"] is None
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "action_validation_failed"
    assert result["error_layer"] == "validation"


def test_plan_node_validates_structured_json_and_repairs_safe_action_alias(
    base_state, fake_device
) -> None:
    model = FakeModelClient(FakeModelResponse("", '{"type":"do","action":"wait","duration":1}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] == {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
    assert "error" not in result


def test_plan_node_validator_rejects_dangerous_structured_field(
    base_state, fake_device
) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"do","action":"Wait","duration":"1 seconds","command":"rm -rf /"}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["finished"] is True
    assert result["action_parsed"] is None
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "action_validation_failed"
    assert result["error_layer"] == "validation"
    assert result["parse_metadata"]["parse_error_code"] == "unsafe_value"


def test_plan_node_rejects_json_action_with_dangerous_provider_field(
    base_state, fake_device
) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            '{"type":"do","action":"tap","x":500,"y":500,"command":"rm -rf /"}',
        )
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["finished"] is True
    assert result["action_parsed"] is None
    assert result["action_result"]["success"] is False
    assert result["parse_metadata"]["parse_error_code"] == "unsafe_value"


def test_json_sensitive_mark_tap_preserves_confirmation_message(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            '{"type":"intent","action":"tap","target_mark_id":"m1"}',
        )
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "screen_marks": [{"mark_id": "m1", "bbox": [400, 400, 600, 600], "text_summary": "支付确认"}],
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] == {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
        "message": "Sensitive mark-grounded tap requires confirmation",
    }


def test_plan_node_grounds_description_intent_with_provider(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    model = FakeModelClient(
        FakeModelResponse(
            "",
            '{"type":"intent","action":"tap","target_text_hint":"设置按钮","target_role":"button"}',
        )
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "grounding_provider": provider,
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [200, 300]}
    assert result["grounding_error"] is None
    assert result["grounding_provider"] == "fake"
    assert result["grounding_observation"]["target"]["has_text_hint"] is True
    assert provider.requests[0]["screen_binding"]["screen_id"] == result["screen_id"]


def test_plan_node_description_grounding_failure_fails_closed(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"intent","action":"tap","target_text_hint":"设置按钮"}')
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "output_mode": "json_schema",
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "provider_unavailable"
    assert result["failure_cause"] == "provider_unavailable"
    assert result["error_layer"] == "grounding"
    assert result["finished"] is True


def test_plan_trace_includes_parse_metadata(base_state, fake_device, tmp_path) -> None:
    import json

    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="parse-meta", trace_dir=tmp_path, redact=False)
    model = FakeModelClient(
        FakeModelResponse(
            "",
            '{"type":"do","action":"Wait","duration":"1 seconds"}',
            {
                "provider": "openai_compatible",
                "configured_mode": "json_schema",
                "detected_format": "json_schema",
                "adapter_used": "json_schema",
                "parse_success": True,
                "parse_error_code": None,
            },
        )
    )

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "trace_writer": writer,
                "verbose": False,
            }
        },
    )

    records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    plan_result = next(item for item in records if item["event"] == "plan_result")
    assert plan_result["payload"]["parse_metadata"]["configured_mode"] == "json_schema"
    assert plan_result["payload"]["parse_metadata"]["adapter_used"] == "json_schema"


def test_plan_trace_parse_metadata_matches_legacy_dsl_parse_failure(
    base_state, fake_device, tmp_path
) -> None:
    import json

    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="parse-fail-meta", trace_dir=tmp_path, redact=False)
    model = FakeModelClient(
        FakeModelResponse(
            "",
            'do(action=__import__("os"))',
            {
                "provider": "openai_compatible",
                "configured_mode": "json_schema",
                "detected_format": "unknown",
                "adapter_used": "none",
                "parse_success": True,
                "parse_error_code": None,
            },
        )
    )

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "trace_writer": writer,
                "verbose": False,
            }
        },
    )

    records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    plan_result = next(item for item in records if item["event"] == "plan_result")
    assert result["action_parsed"] is None
    assert plan_result["payload"]["parse_success"] is False
    assert plan_result["payload"]["parse_metadata"]["parse_success"] is False
    assert plan_result["payload"]["parse_metadata"]["parse_error_code"] == "invalid_json"


def test_reflect_node_cn_and_en_task_finished_detection(
    base_state, fake_device
) -> None:
    for lang, action in (
        ("cn", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"任务已完成"}'),
        ("en", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed"}'),
    ):
        base_state["lang"] = lang
        model = FakeModelClient(FakeModelResponse("ok", action))

        result = reflect_node(
            base_state,
            {
                "configurable": {
                    "model_client": model,
                    "device_factory": fake_device,
                    "verbose": False,
                }
            },
        )

        assert result["action_succeeded"] is True
        assert result["finished"] is True


def test_parse_reflection_action_structured_json_only() -> None:
    structured = parse_reflection_action(
        '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"go_back","message":"页面不对"}'
    )

    assert structured.verdict == "failed"
    assert structured.failure_cause == "wrong_page"
    assert structured.suggested_strategy == "go_back"
    assert structured.message == "页面不对"
    assert parse_reflection_action('{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}').verdict == "succeeded"
    retry = parse_reflection_action('{"verdict":"bad","failure_cause":"bad","suggested_strategy":"bad"}')
    assert retry.verdict == "failed"
    assert retry.failure_cause == "unknown"
    assert retry.suggested_strategy == "retry"
    malformed = parse_reflection_action('reflection(verdict="failed"')
    assert malformed.failure_cause == "unknown"


def test_reflect_node_returns_structured_failure(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "点击后仍停留在错误页面",
            '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"go_back","message":"页面不对"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
            }
        },
    )

    assert result["action_succeeded"] is False
    assert result["reflection_verdict"] == "failed"
    assert result["failure_cause"] == "wrong_page"
    assert result["suggested_strategy"] == "go_back"
    assert result["retry_count"] == 1


def test_reflect_node_hard_verifier_failure_overrides_model_success(base_state, fake_device) -> None:
    base_state["action_result"] = {"success": False, "should_finish": False, "message": "Action failed: boom"}
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["action_succeeded"] is False


def test_reflect_node_does_not_assume_success_when_verifier_unknown_and_model_fails(base_state, fake_device) -> None:
    base_state["screen_hash"] = None
    base_state["screen_id"] = None
    base_state["action_result"] = {"success": True, "message": "ok"}

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": RaisingModelClient(), "device_factory": fake_device, "verbose": False}},
    )

    assert result["verifier_status"] == "unknown"
    assert result["action_succeeded"] is False
    assert result["reflection_verdict"] == "failed"
    assert result["failure_cause"] == "model_reflection_failed"
    assert result["finished"] is False
    assert result["retry_count"] == 1
    assert "secret" not in result["reflection"]


def test_reflect_node_hard_verifier_failure_blocks_finish_from_model(base_state, fake_device) -> None:
    base_state["action_result"] = {"success": False, "should_finish": False, "message": "Action failed: boom"}
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"Task completed"}',
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["reflection_verdict"] == "failed"
    assert result["finished"] is False
    assert result["retry_count"] == 1


def test_reflect_node_updates_gui_memory(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["gui_memory"]["visited_screens"][-1]["screen_id"] == "screen-1"
    assert result["gui_memory"]["tried_actions"][-1]["action"] == "Tap"


def test_reflect_prompt_sanitizes_action_and_result_text(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "13800138000"}
    base_state["action_result"] = {"success": True, "message": "张三"}
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "13800138000" not in text
    assert "张三" not in text


def test_plan_node_includes_structured_reflection_context(base_state, fake_device) -> None:
    base_state["reflection"] = "上一步失败"
    base_state["reflection_verdict"] = "failed"
    base_state["failure_cause"] = "element_not_found"
    base_state["suggested_strategy"] = "swipe_to_find"
    model = FakeModelClient(
        FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}')
    )

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "sys",
            }
        },
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "failure_cause: element_not_found" in text
    assert "suggested_strategy: swipe_to_find" in text


def test_plan_node_observe_mode_does_not_inject_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "observe"
    base_state["screen_belief"] = {"summary": "should-not-appear"}
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "observe"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "短期上下文" not in text
    assert "should-not-appear" not in text


def test_plan_node_inject_mode_adds_bounded_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "inject"
    base_state["screen_belief"] = {"summary": "safe summary", "current_app": "FakeApp"}
    base_state["failure_memory"] = [{"failure_cause": "wrong_page", "action": "Tap"}]
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "短期上下文" in text
    assert "safe summary" in text
    assert "wrong_page" in text
    assert result["context_block_chars"] <= 1500
    assert result["context_strategy"] == "inject_redacted_block"
    assert "screen_belief" in result["selected_sections"]


def test_plan_node_inject_mode_redacts_sensitive_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "inject"
    base_state["screen_belief"] = {"summary": "张三", "visible_text": "13800138000"}
    base_state["summarized_history"] = "sk-secret 明天三点见"
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "张三" not in text
    assert "13800138000" not in text
    assert "sk-secret" not in text


def test_reflect_node_updates_context_memory(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "仍在错误页面",
            '{"verdict":"failed","failure_cause":"context_lost","suggested_strategy":"retry","message":"找不到目标"}',
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["failure_cause"] == "context_lost"
    assert result["screen_belief"]["current_app"] == "FakeApp"
    assert result["failure_memory"][-1]["failure_cause"] == "context_lost"
    assert "context_lost" in result["summarized_history"]


def test_reflection_context_redacts_raw_reflection(base_state, fake_device) -> None:
    base_state["reflection"] = "张三 13800138000"
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "张三" not in text
    assert "13800138000" not in text


def test_plan_node_request_compaction_strips_historical_images_only(
    base_state, fake_device
) -> None:
    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}},
                {"type": "text", "text": "old screen"},
            ],
        },
        {"role": "assistant", "content": 'do(action="Back")'},
    ]
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "observe"}},
    )

    historical_user = model.messages[1]
    latest_user = model.messages[-1]
    assert all(item.get("type") != "image_url" for item in historical_user["content"])
    assert any(item.get("type") == "image_url" for item in latest_user["content"])
    assert base_state["messages"][1]["content"][0]["type"] == "image_url"
    assert result["messages_before"] == 4
    assert result["messages_after"] == 4
    assert result["message_chars_after"] <= result["message_chars_before"]


def test_context_selector_does_not_mutate_action_or_hitl_fields(
    base_state, fake_device
) -> None:
    protected = {
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        "action_raw": 'do(action="Tap", element=[500, 500])',
        "pending_execute": True,
        "interrupt_result": True,
        "action_confirmed": True,
    }
    base_state.update(protected)
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    for key, value in protected.items():
        assert base_state[key] == value
    assert result["action_confirmed"] is False


def test_system_prompt_contains_failure_recovery_map_cn() -> None:
    from phone_agent.config import get_system_prompt

    prompt = get_system_prompt(lang="cn", output_mode="json_schema")
    assert "失败恢复策略" in prompt
    assert "failure_cause" in prompt
    assert "element_not_found" in prompt
    assert "suggested_strategy" in prompt


def test_system_prompt_contains_failure_recovery_map_en() -> None:
    from phone_agent.config import get_system_prompt

    prompt = get_system_prompt(lang="en", output_mode="json_schema")
    assert "Failure recovery" in prompt
    assert "failure_cause" in prompt
    assert "element_not_found" in prompt


def test_step_zero_injects_app_registry_into_system_prompt(base_state, fake_device) -> None:
    base_state["step_count"] = 0
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Back"}'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    assert "可用应用" in system_msg["content"] or "Available Apps" in system_msg["content"]
    assert "Settings" in system_msg["content"]


def test_custom_system_prompt_skips_app_registry(base_state, fake_device) -> None:
    base_state["step_count"] = 0
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Back"}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "system_prompt": "custom prompt",
            }
        },
    )

    system_msg = result["messages"][0]
    assert system_msg["content"] == "custom prompt"
