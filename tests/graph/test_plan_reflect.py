import json
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


@dataclass
class InvalidScreenshot:
    width: int = 1080
    height: int = 2400
    base64_data: str = ""
    mime_type: str = "image/png"
    is_sensitive: bool = True
    is_valid: bool = False
    is_placeholder: bool = True
    failure_code: str = "secure_screenshot_blocked"
    failure_message: str = "secure screen"


class InvalidScreenshotDevice:
    def get_screenshot(self, device_id=None):
        return InvalidScreenshot()

    def get_current_app(self, device_id=None):
        return "SecureApp"


def test_plan_node_fails_closed_on_invalid_screenshot(base_state) -> None:
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"back"}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": InvalidScreenshotDevice(),
                "verbose": False,
            }
        },
    )

    assert model.calls == 0
    assert result["finished"] is True
    assert result["error_code"] == "secure_screenshot_blocked"
    assert result["error_layer"] == "grounding"
    assert result["retry_policy"] == "takeover"
    assert result["failure_cause"] == "unsafe_or_sensitive"
    assert result["screenshot_b64"] is None


def test_reflect_node_fails_closed_on_invalid_screenshot(base_state) -> None:
    model = FakeModelClient(FakeModelResponse("think", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}'))

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": InvalidScreenshotDevice(),
                "verbose": False,
            }
        },
    )

    assert model.calls == 0
    assert result["finished"] is True
    assert result["error_code"] == "secure_screenshot_blocked"
    assert result["error_layer"] == "grounding"
    assert result["retry_policy"] == "takeover"
    assert result["failure_cause"] == "unsafe_or_sensitive"


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


def test_plan_node_wrong_page_parse_failure_falls_back_to_back(base_state, fake_device) -> None:
    model = FakeModelClient(FakeModelResponse("think", "not an action"))
    base_state["failure_cause"] = "wrong_page"
    base_state["suggested_strategy"] = "go_back"

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
    assert result["action_parsed"] == {"_metadata": "do", "action": "Back"}
    assert result["finished"] is False
    assert result["error"] is None
    assert result["parse_metadata"]["deterministic_recovery_action"] == "Back"
    assert result["parse_metadata"]["deterministic_recovery_reason"] == "wrong_page_go_back"


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
    assert result["grounding_error"] is None
    assert result["parse_metadata"]["parse_error_code"] == "mark_required"
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
    assert result["grounding_error"] is None
    assert result["parse_metadata"]["parse_error_code"] == "mark_required"


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
    assert result["grounding_error"] is None
    assert result["parse_metadata"]["parse_error_code"] == "mark_required"
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


def test_plan_node_stores_expected_outcome_as_sibling_contract(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
                    "expected_outcome": {
                        "kind": "loading_finished",
                        "must_not_observe": ["loading", "spinner"],
                    },
                }
            ),
        )
    )

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )

    assert result["action_parsed"] == {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
    assert result["expected_outcome"]["kind"] == "loading_finished"
    assert "expected_outcome" not in result["action_parsed"]


def test_plan_expected_outcome_runtime_contract_verifies_plain_text(base_state, fake_device) -> None:
    plan_model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
                    "expected_outcome": {
                        "kind": "target_appeared",
                        "must_observe": ["搜索"],
                    },
                },
                ensure_ascii=False,
            ),
        )
    )
    planned = plan_node(
        base_state,
        {"configurable": {"model_client": plan_model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )
    assert planned["expected_outcome"]["must_observe"][0].startswith("sha256:")

    reflect_state = {**base_state, **planned, "action_result": {"success": True, "message": "ok"}}
    reflect_model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )
    result = reflect_node(
        reflect_state,
        {
            "configurable": {
                "model_client": reflect_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"


def test_plan_node_redacts_expected_outcome_from_action_raw(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
                    "expected_outcome": {
                        "kind": "target_appeared",
                        "must_observe": ["13800138000"],
                        "target_text_hint": "13800138000",
                    },
                },
                ensure_ascii=False,
            ),
        )
    )

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "13800138000" not in serialized
    assert result["expected_outcome"]["must_observe"] == ["private_text_unverifiable"]


def test_plan_node_action_raw_rebuilds_envelope_without_non_regex_private_text(base_state, fake_device) -> None:
    private_phrase = "张三家庭住址"
    model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
                    "expected_outcome": {
                        "kind": "target_appeared",
                        "must_observe": [private_phrase],
                        "target_text_hint": private_phrase,
                    },
                },
                ensure_ascii=False,
            ),
        )
    )

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )

    assert result["expected_outcome"]["must_observe"][0].startswith("sha256:")
    assert result["expected_outcome"]["target_text_hint"].startswith("sha256:")
    action_raw = json.loads(result["action_raw"])
    assert action_raw["expected_outcome"]["must_observe"][0]["redacted"] is True


def test_plan_node_default_type_outcome_does_not_copy_raw_private_text(base_state, fake_device) -> None:
    model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "type": "do",
                    "action": "Type",
                    "text": "张三的家庭住址",
                },
                ensure_ascii=False,
            ),
        )
    )

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )

    assert result["action_parsed"]["text"] == "张三的家庭住址"
    assert "张三的家庭住址" not in result["action_raw"]
    assert json.loads(result["action_raw"])["action"]["text"]["redacted"] is True
    assert result["expected_outcome"]["kind"] == "text_present"
    assert result["expected_outcome"]["must_observe"] == []


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
    assert result["failure_cause"] == "mark_required"
    assert result["error_layer"] == "grounding"


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


def test_plan_node_builds_provider_marks_before_model_and_uses_mark_id(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    model = FakeModelClient(
        FakeModelResponse(
            "",
            '{"type":"intent","action":"tap","target_mark_id":"fake_1"}',
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
                "mark_provider_hints": ["设置按钮"],
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [200, 300]}
    assert result["grounding_error"] is None
    assert result["grounding_provider"] == "mark_registry"
    assert result["mark_provider_observation"]["provider_count"] == 1
    assert result["mark_registry"]["marks"]["fake_1"]["source"] == "fake"
    assert provider.requests[0]["screen_binding"]["raw_screenshot_hash"] == result["observation"]["snapshot"]["raw_screenshot_hash"]


def test_plan_node_provider_receives_raw_hint_but_prompt_and_observation_are_redacted(
    base_state,
    fake_device,
) -> None:
    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    model = FakeModelClient(
        FakeModelResponse("", '{"type":"intent","action":"tap","target_mark_id":"fake_1"}')
    )
    base_state["task"] = "点击屏幕上的 13800138000 联系人"
    base_state["messages"] = []
    base_state["step_count"] = 0

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

    provider_prompt = provider.requests[0]["raw_hints"][0]
    model_text = model.messages[-1]["content"][-1]["text"]
    observation_raw = json.dumps(result["mark_provider_observation"], ensure_ascii=False)
    registry_raw = json.dumps(result["mark_registry"], ensure_ascii=False)

    assert "13800138000" in provider_prompt
    assert "13800138000" in model_text  # original task remains the raw instruction boundary
    assert "13800138000" not in observation_raw
    assert "13800138000" not in registry_raw
    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [200, 300]}


def test_plan_node_accessibility_marks_failure_does_not_abort(base_state, fake_device) -> None:
    class FailingAccessibilityDevice:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        def get_screen_marks(self, *args, **kwargs):
            raise ValueError("No UiAutomator XML output")

    model = FakeModelClient(FakeModelResponse("", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": FailingAccessibilityDevice(fake_device),
                "output_mode": "json_schema",
                "accessibility_marks": True,
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"]["action"] == "Wait"
    assert result["mark_registry"]["marks"] == {}


def test_plan_node_hybrid_ignores_direct_accessibility_marks_and_uses_provider_gate(base_state, fake_device) -> None:
    class DeviceWithMarks:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        def get_screen_marks(self, *args, **kwargs):
            raise AssertionError("hybrid owns accessibility tree marks")

        @property
        def module(self):
            class Module:
                @staticmethod
                def dump_uiautomator_xml(device_id=None, timeout=None):
                    return """<hierarchy>
                      <node text="设置按钮" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,400][300,800]" />
                    </hierarchy>"""

            return Module

    model = FakeModelClient(FakeModelResponse("", '{"type":"intent","action":"tap","target_mark_id":"ax_1"}'))
    base_state["task"] = "点击设置按钮"

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": DeviceWithMarks(fake_device),
                "output_mode": "json_schema",
                "accessibility_marks": True,
                "grounding_provider_name": "hybrid",
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [200, 300]}
    assert result["mark_provider_observation"]["provider_count"] == 1
    provider_summary = result["mark_provider_observation"]["providers"][0]
    assert provider_summary["provider"] == "accessibility_tree"
    assert provider_summary["metadata"]["fallback_chain"][0]["provider"] == "accessibility_tree"


def test_plan_node_includes_object_registry_sidecars_and_prompt(base_state, fake_device) -> None:
    class DeviceWithFeed:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        @property
        def module(self):
            class Module:
                @staticmethod
                def dump_uiautomator_xml(device_id=None, timeout=None):
                    return """<hierarchy>
                      <node text="" class="android.widget.FrameLayout" enabled="true" bounds="[0,0][1000,2000]">
                        <node text="" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" enabled="true" bounds="[0,200][1000,1800]">
                          <node text="视频标题一" class="android.widget.TextView" clickable="true" enabled="true" bounds="[20,260][980,420]" />
                          <node text="视频标题二" class="android.widget.TextView" clickable="true" enabled="true" bounds="[20,460][980,620]" />
                        </node>
                      </node>
                    </hierarchy>"""

            return Module

    model = FakeModelClient(FakeModelResponse("", '{"type":"intent","action":"tap","object_role":"video","ordinal":1}'))
    base_state["task"] = "打开第一个视频"

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": DeviceWithFeed(fake_device),
                "output_mode": "json_schema",
                "grounding_provider_name": "hybrid",
                "verbose": False,
            }
        },
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "屏幕对象" in text
    assert "primary_mark_id=ax_2" in text
    assert "视频标题一" not in text
    assert "title_hash=5d0fe1cbd1c0" in text
    assert result["action_parsed"] == {"_metadata": "do", "action": "Tap", "element": [500.0, 170.0]}
    assert result["intent_raw"]["ordinal"] == 1
    assert result["object_registry_summary"]["object_count"] >= 2
    assert result["screen_structure_summary"]["node_count"] == 4
    assert "target_object_id" not in result["action_parsed"]
    assert result["expected_outcome"]["selected_object_id_hash"]
    assert result["expected_outcome"]["title_hash"]
    assert result["expected_outcome"]["expected_page_type"] == "detail_or_player"


def test_plan_node_hybrid_filters_unrelated_accessibility_base_path(base_state, fake_device) -> None:
    class DeviceWithUnrelatedMarks:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        def get_screen_marks(self, *args, **kwargs):
            raise AssertionError("hybrid should not inject direct base marks")

        @property
        def module(self):
            class Module:
                @staticmethod
                def dump_uiautomator_xml(device_id=None, timeout=None):
                    return """<hierarchy>
                      <node text="Bluetooth" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,400][300,800]" />
                    </hierarchy>"""

            return Module

    model = FakeModelClient(FakeModelResponse("", '{"type":"intent","action":"tap","target_mark_id":"ax_1"}'))
    base_state["task"] = "点击 Wi-Fi"

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": DeviceWithUnrelatedMarks(fake_device),
                "output_mode": "json_schema",
                "accessibility_marks": True,
                "grounding_provider_name": "hybrid",
                "verbose": False,
            }
        },
    )

    assert result["action_parsed"] is None
    assert result["grounding_error"] == "mark_unavailable"
    assert "ax_1" not in result["mark_registry"]["marks"]


def test_plan_node_description_only_intent_fails_closed(base_state, fake_device) -> None:
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
    assert result["grounding_error"] is None
    assert result["parse_metadata"]["parse_error_code"] == "mark_required"
    assert result["failure_cause"] == "mark_required"
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


def test_expected_outcome_and_verifier_evidence_are_regex_redacted(base_state, fake_device, tmp_path) -> None:
    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="verifier-redaction", trace_dir=tmp_path, redact=False)
    base_state["task"] = "输入手机号 13800138000"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "13800138000"}
    base_state["expected_outcome"] = {
        "kind": "text_present",
        "must_observe": ["13800138000"],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": "13800138000",
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "trace_writer": writer,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_input",
                        "bbox": [100, 100, 900, 180],
                        "role": "EditText",
                        "text_summary": "13800138000",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    serialized_result = json.dumps(result, ensure_ascii=False)
    serialized_trace = writer.path.read_text(encoding="utf-8")
    assert "13800138000" not in serialized_result
    assert "13800138000" not in serialized_trace
    assert '"redacted": true' in serialized_result


def test_reflect_result_stubs_non_regex_vlm_private_text(base_state, fake_device) -> None:
    private_phrase = "张三家庭住址"
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse(
            private_phrase,
            json.dumps(
                {
                    "action_effect": "unknown",
                    "task_progress": private_phrase,
                    "matched_postconditions": [],
                    "missing_postconditions": [],
                    "dynamic_change_only": False,
                    "evidence": private_phrase,
                    "next_strategy": "retry",
                },
                ensure_ascii=False,
            ),
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert private_phrase not in serialized
    assert result["reflection"]["redacted"] is True
    assert result["screen_belief"]["summary"].startswith("verdict=")


def test_plan_trace_parse_metadata_matches_legacy_dsl_parse_failure(
    base_state, fake_device, tmp_path
) -> None:
    import json

    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="parse-fail-meta", trace_dir=tmp_path, redact=False)
    model = FakeModelClient(
        FakeModelResponse(
            "",
            "legacy text action",
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


def test_plan_trace_can_include_raw_model_response_when_enabled(
    base_state, fake_device, tmp_path
) -> None:
    import json

    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(
        trace_id="raw-model-response",
        trace_dir=tmp_path,
        allow_raw_debug=True,
    )

    class RawFailureModel:
        def request(self, *_args, **_kwargs):
            raise ModelParseError(
                "invalid_json",
                {
                    "provider": "openai_compatible",
                    "configured_mode": "json_schema",
                    "detected_format": "unknown",
                    "adapter_used": "none",
                    "parse_success": False,
                    "parse_error_code": "invalid_json",
                    "raw_model_response": "legacy text action",
                    "raw_model_response_length": len("legacy text action"),
                },
            )

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": RawFailureModel(),
                "device_factory": fake_device,
                "trace_writer": writer,
                "verbose": False,
                "parse_retry": 0,
            }
        },
    )

    records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    plan_error = next(item for item in records if item["event"] == "plan_error")
    metadata = plan_error["payload"]["parse_metadata"]
    assert metadata["raw_model_response"] == "legacy text action"
    assert metadata["raw_model_response_length"] == len("legacy text action")


def test_plan_error_trace_includes_observation_sidecar_summaries(
    base_state, fake_device, tmp_path
) -> None:
    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(trace_id="plan-error-sidecars", trace_dir=tmp_path)
    base_state["task"] = "搜索"

    class ParseFailureModel:
        def request(self, *_args, **_kwargs):
            raise ModelParseError("invalid_json", {"parse_error_code": "invalid_json"})

    class DeviceWithTree:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        @property
        def module(self):
            class Module:
                @staticmethod
                def dump_uiautomator_xml(device_id=None, timeout=None):
                    return """<hierarchy>
                      <node text="搜索" class="android.widget.TextView" clickable="true" enabled="true" bounds="[100,100][500,200]" />
                    </hierarchy>"""

            return Module

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": ParseFailureModel(),
                "device_factory": DeviceWithTree(fake_device),
                "trace_writer": writer,
                "verbose": False,
                "parse_retry": 0,
                "grounding_provider_name": "hybrid",
            }
        },
    )

    records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    plan_error = next(item for item in records if item["event"] == "plan_error")
    payload = plan_error["payload"]
    assert payload["mark_provider_observation"]["provider_count"] == 1
    assert payload["screen_structure_summary"]["node_count"] == 1
    assert payload["object_registry_summary"]["object_count"] == 1


def test_reflect_node_cn_and_en_task_finished_detection(
    base_state, fake_device
) -> None:
    """Reflect correctly parses CN/EN finish suggestions.

    Under the P0-3 fix, model self-attestation (suggested_strategy=finish on a
    non-pending_finish do action) is blocked — only pending_finish can lead to
    finished=True.  This test verifies parsing, not the finish gate.
    """
    for lang, action in (
        (
            "cn",
            '{"action_effect":"succeeded","task_progress":"任务已完成",'
            '"matched_postconditions":["complete"],"missing_postconditions":[],'
            '"dynamic_change_only":false,"evidence":"done","next_strategy":"finish"}',
        ),
        (
            "en",
            '{"action_effect":"succeeded","task_progress":"Task completed",'
            '"matched_postconditions":["complete"],"missing_postconditions":[],'
            '"dynamic_change_only":false,"evidence":"done","next_strategy":"finish"}',
        ),
    ):
        base_state["lang"] = lang
        base_state["expected_outcome"] = {
            "kind": "generic",
            "must_observe": [],
            "must_not_observe": [],
            "target_mark_id": None,
            "target_text_hint": None,
            "timeout_hint": None,
            "dynamic_regions": [],
        }
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
        # P0-3: non-pending_finish model self-attestation must not finish
        assert result["finished"] is False
        assert result["failure_cause"] == "goal_not_satisfied"


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


def test_parse_reflection_action_rejects_success_with_missing_postconditions() -> None:
    parsed = parse_reflection_action(
        '{"action_effect":"succeeded","task_progress":"not finished",'
        '"matched_postconditions":[],"missing_postconditions":["focused_editable_or_keyboard_visible"],'
        '"dynamic_change_only":false,"evidence":"search text only","next_strategy":"continue"}'
    )

    assert parsed.verdict == "failed"
    assert parsed.failure_cause == "wrong_page"


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


def test_reflect_node_does_not_finish_on_not_finished_task_progress(base_state, fake_device) -> None:
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"action_effect":"succeeded","task_progress":"not finished",'
            '"matched_postconditions":["generic_progress"],"missing_postconditions":[],'
            '"dynamic_change_only":false,"evidence":"intermediate page","next_strategy":"continue"}',
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["reflection_verdict"] == "succeeded"
    assert result["finished"] is False


def test_reflect_node_not_finished_task_progress_overrides_finish_strategy(base_state, fake_device) -> None:
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"action_effect":"succeeded","task_progress":"not finished",'
            '"matched_postconditions":["generic_progress"],"missing_postconditions":[],'
            '"dynamic_change_only":false,"evidence":"intermediate page","next_strategy":"finish"}',
        )
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["reflection_verdict"] == "partial"
    assert result["suggested_strategy"] == "continue"
    assert result["finished"] is False


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


def test_reflect_node_screen_change_is_only_weak_signal(base_state, fake_device) -> None:
    base_state["screen_hash"] = "before-screen"
    base_state["expected_outcome"] = {
        "kind": "target_appeared",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["banner", "recommendation_feed"],
    }
    base_state["observation"] = {"visible_text": ["首页推荐", "广告", "热词换一换"]}
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["verifier_status"] == "unknown"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["missing_postconditions"] == ["after_observation_unavailable"]


def test_reflect_node_swipe_hash_change_is_not_success(base_state, fake_device) -> None:
    base_state["screen_hash"] = "before-screen"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Swipe", "start": [500, 800], "end": [500, 200]}
    base_state["expected_outcome"] = {
        "kind": "content_shifted",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": ["banner", "recommendation_feed"],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    assert result["verifier_status"] == "unknown"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["weak_signals"]["screen_changed"] is True
    assert result["verifier_evidence"]["missing_postconditions"] == ["content_shift_unverified"]


def test_reflect_node_launch_matches_package_alias(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Launch", "app": "设置"}
    base_state["expected_outcome"] = {
        "kind": "app_opened",
        "must_observe": ["sha256:aa"],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "top_activity": "com.android.settings/.Settings",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"
    assert result["verifier_result"]["signals"]["launch_matched"] is True


def test_private_expected_text_does_not_hash_match_different_private_text(base_state, fake_device) -> None:
    plan_model = FakeModelClient(
        FakeModelResponse(
            "",
            json.dumps(
                {
                    "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
                    "expected_outcome": {
                        "kind": "target_appeared",
                        "must_observe": ["13800138000"],
                    },
                },
                ensure_ascii=False,
            ),
        )
    )
    planned = plan_node(
        base_state,
        {"configurable": {"model_client": plan_model, "device_factory": fake_device, "output_mode": "json_schema"}},
    )
    assert planned["expected_outcome"]["must_observe"] == ["private_text_unverifiable"]

    reflect_state = {**base_state, **planned, "action_result": {"success": True, "message": "ok"}}
    reflect_model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )
    result = reflect_node(
        reflect_state,
        {
            "configurable": {
                "model_client": reflect_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_phone",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "13900139000",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["missing_postconditions"] == ["private_text_unverifiable"]


def test_reflect_node_type_text_postcondition_success(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "hello"}
    base_state["expected_outcome"] = {
        "kind": "text_present",
        "must_observe": ["hello"],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["observation"] = {"nodes": [{"role": "EditText", "text_summary": "before-only"}]}
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_input",
                        "bbox": [100, 100, 900, 180],
                        "role": "EditText",
                        "text_summary": "hello",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"
    assert result["action_succeeded"] is True
    assert result["verifier_evidence"]["matched_postconditions"][0]["redacted"] is True
    assert result["verifier_evidence"]["matched_postconditions"][0]["sha256"] == "2cf24dba5fb0"


def test_reflect_node_hash_matches_text_segment(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "target_appeared",
        "must_observe": ["sha256:44ce7ae909bb"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "sha256:44ce7ae909bb",
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search_button",
                        "bbox": [100, 100, 900, 180],
                        "role": "Button",
                        "text_summary": "搜索按钮",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["verifier_evidence"]["matched_postconditions"] == ["sha256:44ce7ae909bb"]


def test_reflect_node_selected_object_hash_matches_detail_page(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 300]}
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 1,
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"retry","message":"model missed"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "title", "bbox": [50, 100, 900, 180], "role": "TextView", "text_summary": "视频标题一"},
                    {"mark_id": "player", "bbox": [0, 200, 1000, 800], "role": "Button", "text_summary": "播放器 暂停 弹幕"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"
    assert result["verifier_evidence"]["selected_object_signals"]["selected_object_match"] is True


def test_reflect_node_selected_object_detects_wrong_detail(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 300]}
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 1,
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "other", "bbox": [50, 100, 900, 180], "role": "TextView", "text_summary": "其他标题"},
                    {"mark_id": "player", "bbox": [0, 200, 1000, 800], "role": "Button", "text_summary": "播放器 暂停 弹幕"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["selected_object_signals"]["wrong_detail_opened"] is True


def test_reflect_node_selected_object_detects_still_on_feed(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 300]}
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 1,
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "feed", "bbox": [0, 0, 1000, 100], "role": "TextView", "text_summary": "首页 推荐"},
                    {"mark_id": "title", "bbox": [50, 300, 900, 420], "role": "TextView", "text_summary": "视频标题一"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["selected_object_signals"]["same_surface_still_visible"] is True


def test_reflect_node_selected_object_feed_terms_win_over_generic_player_terms(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 300]}
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 1,
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "feed", "bbox": [0, 0, 1000, 100], "role": "TextView", "text_summary": "首页 推荐"},
                    {"mark_id": "title", "bbox": [50, 300, 900, 420], "role": "TextView", "text_summary": "视频标题一"},
                    {"mark_id": "actions", "bbox": [50, 430, 900, 520], "role": "TextView", "text_summary": "评论 播放"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    signals = result["verifier_evidence"]["selected_object_signals"]
    assert signals["selected_object_hash_match"] is True
    assert signals["selected_object_detail_signal"] is True
    assert signals["same_surface_still_visible"] is True
    assert "selected_object_match" not in signals


def test_reflect_node_input_focused_requires_focus_or_keyboard_signal(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"partial","failure_cause":"unknown","suggested_strategy":"retry","message":"not enough"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "unknown"
    assert result["reflection_verdict"] == "partial"
    assert result["verifier_evidence"]["missing_postconditions"] == [
        "focused_editable_or_keyboard_visible"
    ]


def test_reflect_node_search_page_progress_prevents_takeover(base_state, fake_device) -> None:
    base_state["retry_count"] = 2
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "逗比的雀巢"}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索", "取消"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "not enough",
            '{"verdict":"partial","failure_cause":"unknown","suggested_strategy":"continue","message":"progress"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "verifier_takeover_threshold": 3,
                "top_activity": "tv.danmaku.bili/com.bilibili.search2.main.BiliMainSearchActivity",
                "after_screen_marks": [
                    {
                        "mark_id": "search_box",
                        "bbox": [50, 60, 780, 160],
                        "role": "EditText",
                        "text_summary": "逗比的雀巢",
                    },
                    {
                        "mark_id": "search_button",
                        "bbox": [800, 60, 950, 160],
                        "role": "Button",
                        "text_summary": "Search",
                    },
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "unknown"
    assert result["retry_count"] == 0
    assert result["suggested_strategy"] == "continue"
    assert result.get("pending_interrupt") is None
    assert result["verifier_evidence"]["progress_signals"]["typed_text_present"] is True
    assert result["verifier_evidence"]["progress_signals"]["search_button_present"] is True
    assert result["verifier_evidence"]["progress_signals"]["search_activity"] is True
    assert result["verifier_evidence"]["progress_signals"]["strong_progress"] is True


def test_reflect_node_search_chrome_without_typed_text_still_takeover(base_state, fake_device) -> None:
    base_state["retry_count"] = 2
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "逗比的雀巢"}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "not enough",
            '{"verdict":"partial","failure_cause":"unknown","suggested_strategy":"continue","message":"weak progress"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "verifier_takeover_threshold": 3,
                "top_activity": "tv.danmaku.bili/com.bilibili.search2.main.BiliMainSearchActivity",
                "after_screen_marks": [
                    {
                        "mark_id": "search_box",
                        "bbox": [50, 60, 780, 160],
                        "role": "EditText",
                        "text_summary": "搜索",
                    },
                    {
                        "mark_id": "search_button",
                        "bbox": [800, 60, 950, 160],
                        "role": "Button",
                        "text_summary": "Search",
                    },
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "unknown"
    assert result["retry_count"] == 3
    assert result["suggested_strategy"] == "takeover"
    assert result["pending_interrupt"] == "takeover"
    assert result["verifier_evidence"]["progress_signals"]["search_button_present"] is True
    assert "strong_progress" not in result["verifier_evidence"]["progress_signals"]


def test_reflect_node_keyboard_residue_does_not_suppress_page_takeover(base_state, fake_device) -> None:
    base_state["retry_count"] = 2
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 500]}
    base_state["expected_outcome"] = {
        "kind": "target_appeared",
        "must_observe": ["sha256:0123456789ab"],
        "must_not_observe": [],
        "target_mark_id": "result",
        "target_text_hint": "sha256:0123456789ab",
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "not enough",
            '{"verdict":"partial","failure_cause":"wrong_page","suggested_strategy":"continue","message":"keyboard residue"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "verifier_takeover_threshold": 3,
                "keyboard_visible": True,
                "after_screen_marks": [
                    {
                        "mark_id": "search_box",
                        "bbox": [50, 60, 780, 160],
                        "role": "EditText",
                        "text_summary": "old query",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["retry_count"] == 3
    assert result["pending_interrupt"] == "takeover"
    assert "strong_progress" not in result["verifier_evidence"]["progress_signals"]


def test_reflect_node_vlm_success_with_missing_postconditions_does_not_succeed(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "looks ok",
            '{"action_effect":"succeeded","task_progress":"not finished",'
            '"matched_postconditions":[],"missing_postconditions":["focused_editable_or_keyboard_visible"],'
            '"dynamic_change_only":false,"evidence":"search text visible only","next_strategy":"continue"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "unknown"
    assert result["reflection_verdict"] == "failed"
    assert result["action_succeeded"] is False
    assert result["finished"] is False


def test_reflect_node_input_focused_succeeds_with_keyboard_signal(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "keyboard_visible": True,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "EditText",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"
    assert result["verifier_result"]["signals"]["keyboard_visible"] is True


def test_reflect_node_input_focused_succeeds_with_focused_editable_signal(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "input_focused",
        "must_observe": ["搜索"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "搜索",
        "timeout_hint": None,
        "dynamic_regions": ["hot_words"],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "focused_editable": True,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "EditText",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "success"
    assert result["reflection_verdict"] == "succeeded"
    assert result["verifier_result"]["signals"]["focused_editable"] is True


def test_reflect_node_does_not_use_stale_before_observation_for_postcondition(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "hello"}
    base_state["expected_outcome"] = {
        "kind": "text_present",
        "must_observe": ["hello"],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["observation"] = {"nodes": [{"role": "EditText", "text_summary": "hello"}]}
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_input",
                        "bbox": [100, 100, 900, 180],
                        "role": "EditText",
                        "text_summary": "different",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["missing_postconditions"][0]["redacted"] is True
    assert result["verifier_evidence"]["missing_postconditions"][0]["sha256"] == "2cf24dba5fb0"


def test_reflect_node_metadata_only_mark_id_does_not_satisfy_postcondition(base_state, fake_device) -> None:
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [500, 120]}
    base_state["expected_outcome"] = {
        "kind": "target_appeared",
        "must_observe": ["search"],
        "must_not_observe": [],
        "target_mark_id": "search",
        "target_text_hint": "search",
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse(
            "not enough",
            '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"retry","message":"not visible"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "search",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "home feed",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["verifier_status"] == "failure"
    assert result["reflection_verdict"] == "failed"
    assert result["verifier_evidence"]["missing_postconditions"][0]["redacted"] is True


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


def test_reflect_node_repeated_failure_routes_to_takeover_interrupt(base_state, fake_device) -> None:
    base_state["retry_count"] = 1
    model = FakeModelClient(
        FakeModelResponse(
            "still wrong",
            '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"retry","message":"still wrong"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "verifier_takeover_threshold": 2,
            }
        },
    )

    assert result["retry_count"] == 2
    assert result["suggested_strategy"] == "takeover"
    assert result["pending_interrupt"] == "takeover"
    assert result["hitl_count"] == 1
    assert result["finished"] is False


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
    base_state["task"] = "给 13800138000 发短信"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "13800138000"}
    base_state["action_result"] = {"success": True, "message": "已发送短信至13900139000"}
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"continue","message":"ok"}')
    )

    reflect_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "verbose": False}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "原始任务：" in text
    assert "13800138000" not in text
    derived_text = text.split("刚执行的动作：", 1)[1]
    assert "13900139000" not in text
    assert "redacted" in text


def test_reflect_prompt_stubs_non_regex_action_and_ui_text(base_state, fake_device) -> None:
    private_phrase = "张三家庭住址"
    base_state["task"] = "输入地址"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "message": private_phrase}
    base_state["expected_outcome"] = {
        "kind": "target_appeared",
        "must_observe": ["sha256:000000000000"],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": "sha256:000000000000",
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}')
    )

    reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_input",
                        "bbox": [100, 100, 900, 180],
                        "role": "EditText",
                        "text_summary": private_phrase,
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert private_phrase not in text
    assert "redacted" in text


def test_reflect_prompt_includes_before_after_observation_summaries(base_state, fake_device) -> None:
    base_state["observation"] = {
        "snapshot": {
            "screen_id": "before-screen",
            "screen_hash": "before-hash",
            "current_app": "FakeApp",
        },
        "mark_registry": {
            "marks": {
                "before_search": {
                    "mark_id": "before_search",
                    "role": "TextView",
                    "text_summary": "首页推荐",
                }
            }
        },
        "mark_provider_observation": {"provider": "before", "candidate_count": 1},
    }
    base_state["screen_hash"] = None
    base_state["screen_id"] = None
    model = FakeModelClient(
        FakeModelResponse("ok", '{"verdict":"failed","failure_cause":"wrong_page","suggested_strategy":"retry","message":"still home"}')
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "after_search",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "搜索",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "动作前观测摘要" in text
    assert "before_search" in text
    assert "动作后观测摘要" in text
    assert "after_search" in text
    assert "首页推荐" not in text
    assert "搜索" not in text
    assert result["finished"] is False
    assert base_state["messages"][0]["content"][1]["type"] == "image_url"


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


def test_plan_node_can_trace_unredacted_prompt_debug(base_state, fake_device, tmp_path) -> None:
    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(
        trace_id="prompt-debug",
        trace_dir=tmp_path,
        allow_raw_request_debug=True,
    )
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "trace_writer": writer,
                "trace_request_messages": True,
                "trace_prompt_blocks": True,
                "trace_unredacted_prompt": True,
            }
        },
    )

    records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    prompt_debug = next(item for item in records if item["event"] == "plan_prompt_debug")
    payload = prompt_debug["payload"]
    assert payload["request_messages"][-1]["content"][-1]["text"]
    assert payload["prompt_blocks"]["task"] == base_state["task"]
    assert payload["prompt_block_chars"]["marks_block"] >= 0


def test_plan_node_inject_mode_redacts_sensitive_context(base_state, fake_device) -> None:
    base_state["context_mode"] = "inject"
    base_state["screen_belief"] = {"summary": "允许存储权限", "visible_text": "13800138000"}
    base_state["summarized_history"] = "sk-secret 明天三点见"
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "13800138000" not in text
    assert "sk-secret" not in text
    assert "允许存储权限" in text


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
    base_state["reflection"] = "张三 13800138000 请重试"
    base_state["context_mode"] = "inject"
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "张三" in text  # regex-only preserves non-sensitive Chinese text
    assert "13800138000" not in text  # phone number regex-redacted
    assert "请重试" in text


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
        {"role": "assistant", "content": '{"type":"do","action":"back"}'},
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
        "action_raw": '{"_metadata":"do","action":"Tap","element":[500,500]}',
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


def test_plan_node_injects_task_goal_after_message_compaction(base_state, fake_device) -> None:
    from phone_agent.graph.goal_compiler import HeuristicGoalCompiler

    base_state["task"] = "去b站看逗比的雀巢的第二个视频"
    # Inject a heuristic goal contract to match the task
    base_state["goal_contract"] = HeuristicGoalCompiler().compile(task=base_state["task"]).to_dict()
    base_state["goal_contract_status"] = "compiled"
    base_state["step_count"] = 3
    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        *[
            {"role": "assistant" if index % 2 else "user", "content": f"history-{index}"}
            for index in range(10)
        ],
    ]
    model = FakeModelClient(FakeModelResponse("think", '{"type":"do","action":"Wait","duration":"1 seconds"}'))

    result = plan_node(
        base_state,
        {"configurable": {"model_client": model, "device_factory": fake_device, "context_mode": "inject"}},
    )

    text = model.messages[-1]["content"][-1]["text"]
    assert "任务目标契约" in text
    assert "bilibili" in text  # target_app_hint from heuristic compiler
    assert "vlm_judge_at_finish" in text  # verification_strategy
    # P0-2: plan no longer writes goal_contract to state (goal_node owns it)
    assert "goal_contract" not in result or result.get("goal_contract") is None
    # But the contract in base_state should be preserved (plan doesn't overwrite)
    assert base_state["goal_contract"]["target_app_hint"] == "bilibili"
    assert base_state["goal_contract"]["ordinal"] == 2


def test_reflect_node_rejects_pending_finish_without_final_goal_evidence(base_state, fake_device) -> None:
    base_state["task"] = "去b站看逗比的雀巢的第二个视频"
    base_state["action_parsed"] = {"_metadata": "finish", "message": "已搜索到UP主"}
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "已搜索到UP主"}
    base_state["pending_finish"] = True
    model = FakeModelClient(FakeModelResponse("unused", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"done"}'))

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "tab", "bbox": [0, 0, 1000, 100], "role": "TextView", "text_summary": "搜索结果 综合 视频"},
                    {"mark_id": "up", "bbox": [50, 200, 900, 260], "role": "TextView", "text_summary": "UP主"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is False
    assert result["pending_finish"] is False
    assert result["finish_validation_status"] in {"failure", "unknown"}
    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["suggested_strategy"] == "continue"


def test_reflect_node_accepts_pending_finish_with_final_goal_evidence(base_state, fake_device) -> None:
    from phone_agent.graph.goal import GoalContract, SuccessCriterion

    base_state["task"] = "去b站看逗比的雀巢的第二个视频"
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "已打开第二个视频",
        "matched_terminal_evidence": ["player_visible", "selected_rank_2"],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "已打开第二个视频"}
    base_state["pending_finish"] = True
    base_state["goal_contract"] = GoalContract(
        task_hash="h",
        redacted_objective="看b站第二个视频",
        objective_length=14,
        success_criteria=[
            SuccessCriterion(name="player_visible", description="播放器可见", verification="vlm_judge", required=True),
            SuccessCriterion(name="selected_rank_2", description="第2个视频", verification="object_rank_match", required=True),
        ],
        target_app_hint="bilibili",
        ordinal=2,
        verification_strategy="hybrid",
        compile_status="compiled",
        compile_source="external",
    ).to_dict()
    base_state["goal_contract_status"] = "compiled"
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 2,
    }
    model = FakeModelClient(FakeModelResponse("unused", '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"done","named_evidence":[{"criterion":"player_visible","screen_reference":"mark_id=player"}]}'))

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "title", "bbox": [50, 100, 900, 180], "role": "TextView", "text_summary": "视频标题一"},
                    {"mark_id": "player", "bbox": [0, 200, 1000, 800], "role": "Button", "text_summary": "播放器 暂停 弹幕 评论"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    assert result["failure_cause"] is None



def test_expected_outcome_selected_video_object_defaults_to_page_opened(base_state, fake_device) -> None:
    class DeviceWithVideoObject:
        def __init__(self, delegate):
            self.delegate = delegate

        def get_screenshot(self, device_id=None):
            return self.delegate.get_screenshot(device_id)

        def get_current_app(self, device_id=None):
            return self.delegate.get_current_app(device_id)

        @property
        def module(self):
            class Module:
                @staticmethod
                def dump_uiautomator_xml(device_id=None, timeout=None):
                    return """<hierarchy>
                      <node text="" class="android.widget.FrameLayout" enabled="true" bounds="[0,0][1000,2000]">
                        <node text="" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" enabled="true" bounds="[0,200][1000,1800]">
                          <node text="视频标题一" class="android.widget.TextView" clickable="true" enabled="true" bounds="[20,260][980,420]" />
                          <node text="视频标题二" class="android.widget.TextView" clickable="true" enabled="true" bounds="[20,460][980,620]" />
                        </node>
                      </node>
                    </hierarchy>"""

            return Module

    model = FakeModelClient(FakeModelResponse("", '{"type":"intent","action":"tap","object_role":"video","ordinal":1}'))
    base_state["task"] = "打开第一个视频"

    result = plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": DeviceWithVideoObject(fake_device),
                "output_mode": "json_schema",
                "grounding_provider_name": "hybrid",
                "verbose": False,
            }
        },
    )

    assert result["expected_outcome"]["kind"] == "page_opened"
    assert result["expected_outcome"]["object_type"] == "video"
    assert result["expected_outcome"]["expected_page_type"] == "detail_or_player"


def test_reflect_node_generic_pending_finish_can_use_model_evidence(base_state, fake_device) -> None:
    from phone_agent.graph.goal import GoalContract, SuccessCriterion

    base_state["task"] = "完成普通页面任务"
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "已完成",
        "matched_terminal_evidence": ["task_done"],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "已完成"}
    base_state["pending_finish"] = True
    base_state["goal_contract"] = GoalContract(
        task_hash="h",
        redacted_objective="完成普通页面任务",
        objective_length=8,
        success_criteria=[
            SuccessCriterion(name="task_done", description="完成标识可见", verification="vlm_judge", required=True),
        ],
        verification_strategy="vlm_judge_at_finish",
        compile_status="compiled",
        compile_source="external",
    ).to_dict()
    base_state["goal_contract_status"] = "compiled"
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish",'
            '"message":"完成标识可见","named_evidence":'
            '[{"criterion":"task_done","screen_reference":"mark_id=done"}]}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "done", "bbox": [50, 60, 950, 160], "role": "TextView", "text_summary": "已完成"}
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    assert "task_done" in result["finish_validation_evidence"]["matched_terminal_evidence"]


def test_reflect_node_rejects_ranked_finish_without_expected_rank_match(base_state, fake_device) -> None:
    from phone_agent.graph.goal import GoalContract, SuccessCriterion

    base_state["task"] = "去b站看逗比的雀巢的第二个视频"
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "已打开视频",
        "matched_terminal_evidence": ["player_visible", "selected_rank_2"],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "已打开视频"}
    base_state["pending_finish"] = True
    base_state["goal_contract"] = GoalContract(
        task_hash="h",
        redacted_objective="看b站第二个视频",
        objective_length=14,
        success_criteria=[
            SuccessCriterion(name="player_visible", description="播放器可见", verification="vlm_judge", required=True),
            SuccessCriterion(name="selected_rank_2", description="第2个视频", verification="object_rank_match", required=True),
        ],
        target_app_hint="bilibili",
        ordinal=2,
        verification_strategy="hybrid",
        compile_status="compiled",
        compile_source="external",
    ).to_dict()
    base_state["goal_contract_status"] = "compiled"
    # expected_rank = 1 but ordinal = 2 → rank mismatch
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": "m1",
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
        "object_type": "video",
        "object_evidence_hash": "5d0fe1cbd1c0",
        "title_hash": "5d0fe1cbd1c0",
        "expected_page_type": "detail_or_player",
        "expected_rank": 1,
    }
    model = FakeModelClient(
        FakeModelResponse(
            "unused",
            '{"verdict":"succeeded","failure_cause":"none","suggested_strategy":"finish","message":"done",'
            '"named_evidence":[{"criterion":"player_visible","screen_reference":"mark_id=player"}]}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {"mark_id": "title", "bbox": [50, 100, 900, 180], "role": "TextView", "text_summary": "视频标题一"},
                    {"mark_id": "player", "bbox": [0, 200, 1000, 800], "role": "Button", "text_summary": "播放器 暂停 弹幕 评论"},
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is False
    assert result["finish_validation_status"] in {"failure", "unknown"}
    assert result["failure_cause"] == "goal_not_satisfied"
    assert "selected_rank_2" in result["finish_validation_evidence"]["missing_terminal_evidence"]
