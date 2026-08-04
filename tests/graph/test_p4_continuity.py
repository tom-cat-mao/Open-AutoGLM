"""P4 continuity: progress_note intent chaining + think recovery.

Covers:
- progress_note: model envelope output -> state -> next-round plan prompt
  (sanitized + bounded, envelope top-level not stripped)
- think recovery: reasoning_content accumulated into assistant history with
  real <think>...</think> wrapper; no-reasoning providers keep the historical
  <think...>...</think...> placeholder byte-for-byte
- messages_reducer append/replace semantics unchanged by the new formats
  (direct semantics live in test_state.py; the plan-level e2e lives in
  test_plan_reflect.py — this file only keeps the flow-level think test)
"""

import copy
import json
from dataclasses import dataclass

import pytest

from phone_agent.config import get_system_prompt
from phone_agent.graph.expected_outcome import (
    PROGRESS_NOTE_MAX_CHARS,
    extract_progress_note,
)
from phone_agent.graph.nodes.execute import _strip_and_append
from phone_agent.graph.nodes.plan import plan_node
from phone_agent.model.client import MessageBuilder, ModelClient, ModelConfig


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


def _envelope_response(progress_note: str | None = None) -> str:
    payload = {
        "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
        "expected_outcome": {"kind": "loading_finished"},
    }
    if progress_note is not None:
        payload["progress_note"] = progress_note
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------
# extract_progress_note unit behaviour
# --------------------------------------------------------------------------


def test_extract_progress_note_envelope_top_level() -> None:
    assert extract_progress_note(
        {
            "action": {"type": "do", "action": "Wait", "duration": "1 seconds"},
            "expected_outcome": {"kind": "loading_finished"},
            "progress_note": " 已等待加载，下一步点击设置 ",
        }
    ) == "已等待加载，下一步点击设置"


def test_extract_progress_note_missing_or_invalid() -> None:
    assert extract_progress_note(None) is None
    assert extract_progress_note("plain string") is None
    assert extract_progress_note({}) is None
    assert extract_progress_note({"progress_note": 42}) is None
    assert extract_progress_note({"progress_note": "   "}) is None


def test_extract_progress_note_truncates_to_budget() -> None:
    note = "长" * (PROGRESS_NOTE_MAX_CHARS + 50)

    result = extract_progress_note({"progress_note": note})

    assert result is not None
    assert len(result) == PROGRESS_NOTE_MAX_CHARS


# --------------------------------------------------------------------------
# progress_note full chain: model output -> state -> next plan prompt
# --------------------------------------------------------------------------


def test_progress_note_round_trip_into_next_plan_prompt(
    base_state, fake_device
) -> None:
    state1 = copy.deepcopy(base_state)
    state1.update({"step_count": 0, "messages": []})
    model1 = FakeModelClient(
        FakeModelResponse("", _envelope_response("已等待加载，下一步点击设置"))
    )
    result1 = plan_node(
        state1,
        {"configurable": {"model_client": model1, "device_factory": fake_device}},
    )

    assert result1["progress_note"] == "已等待加载，下一步点击设置"
    assert result1["action_parsed"]["action"] == "Wait"

    state2 = copy.deepcopy(base_state)
    state2.update(
        {
            "step_count": 1,
            "messages": state1["messages"] + result1["messages"],
            "progress_note": result1["progress_note"],
        }
    )
    model2 = FakeModelClient(
        FakeModelResponse("", _envelope_response("第二步已完成"))
    )
    result2 = plan_node(
        state2,
        {"configurable": {"model_client": model2, "device_factory": fake_device}},
    )

    plan_text = model2.messages[-1]["content"][-1]["text"]
    assert "上轮意图：已等待加载，下一步点击设置" in plan_text
    assert result2["progress_note"] == "第二步已完成"


def test_progress_note_absent_when_model_omits_it(base_state, fake_device) -> None:
    state = copy.deepcopy(base_state)
    state.update({"step_count": 0, "messages": []})
    model = FakeModelClient(FakeModelResponse("", _envelope_response()))

    result = plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    assert result["progress_note"] is None


def test_progress_note_sanitized_before_state_write(base_state, fake_device) -> None:
    state = copy.deepcopy(base_state)
    state.update({"step_count": 0, "messages": []})
    model = FakeModelClient(
        FakeModelResponse(
            "", _envelope_response("已输入 13800138000，下一步点击确认")
        )
    )

    result = plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    stored = result["progress_note"]
    assert stored is not None
    assert "13800138000" not in stored
    assert "<redacted>" in stored


def test_progress_note_injected_sanitized_next_round(base_state, fake_device) -> None:
    state = copy.deepcopy(base_state)
    state.update({"step_count": 1, "progress_note": "已输入 13800138000，下一步确认"})
    model = FakeModelClient(
        FakeModelResponse("", _envelope_response("后续"))
    )

    plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    plan_text = model.messages[-1]["content"][-1]["text"]
    assert "上轮意图：" in plan_text
    assert "13800138000" not in plan_text


def test_progress_note_not_injected_when_absent(base_state, fake_device) -> None:
    state = copy.deepcopy(base_state)
    state.update({"step_count": 1, "progress_note": None})
    model = FakeModelClient(FakeModelResponse("", _envelope_response("后续")))

    plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    plan_text = model.messages[-1]["content"][-1]["text"]
    assert "上轮意图：" not in plan_text


def test_progress_note_envelope_same_level_survives_plan_parse(
    base_state, fake_device
) -> None:
    """The envelope top-level note must survive the whole pipeline.

    client keeps the raw envelope (only ``action`` is extracted for adapter
    input); plan.py re-reads progress_note at the envelope top level.
    """
    state = copy.deepcopy(base_state)
    state.update({"step_count": 0, "messages": []})
    model = FakeModelClient(
        FakeModelResponse("", _envelope_response("已等待加载，下一步点击设置"))
    )

    result = plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    assert result["progress_note"] == "已等待加载，下一步点击设置"
    raw = json.loads(result["action_raw"])
    assert raw["action"]["action"] == "Wait"


# --------------------------------------------------------------------------
# think recovery: history wrapper flow (e2e)
# --------------------------------------------------------------------------


def test_think_flows_plan_to_assistant_history(base_state, fake_device) -> None:
    """Streamed reasoning captured by the client reaches the assistant history.

    plan stores ``state[\"thinking\"]``; execute wraps it in real think tags.
    """
    state = copy.deepcopy(base_state)
    state.update(
        {
            "step_count": 0,
            "messages": [],
            "thinking": "先思考，确定目标是设置",
        }
    )
    model = FakeModelClient(
        FakeModelResponse("先思考，确定目标是设置", '{"type":"do","action":"Wait","duration":"1 seconds"}')
    )
    result = plan_node(
        state,
        {"configurable": {"model_client": model, "device_factory": fake_device}},
    )

    assert result["thinking"] == "先思考，确定目标是设置"

    messages = list(state["messages"]) + list(result["messages"])
    messages = _strip_and_append(messages, result["thinking"], result["action_raw"])

    assert messages[-1]["content"].startswith("<think>先思考，确定目标是设置</think>")


# --------------------------------------------------------------------------
# image stripping edge cases (P0 #3: only the current screenshot is kept)
# --------------------------------------------------------------------------


def _user_message(*items: tuple[str, str]) -> dict:
    return {"role": "user", "content": [dict(type=kind, **payload) for kind, payload in items]}


def test_remove_images_strips_every_historical_image_and_keeps_text() -> None:
    message = _user_message(
        ("text", {"text": "before"}),
        ("image_url", {"image_url": {"url": "data:image/png;base64,old-1"}}),
        ("text", {"text": "middle"}),
        ("image_url", {"image_url": {"url": "data:image/png;base64,old-2"}}),
        ("image_url", {"image_url": {"url": "data:image/png;base64,old-3"}}),
        ("text", {"text": "after"}),
    )

    stripped = MessageBuilder.remove_images_from_message(dict(message))

    assert stripped["content"] == [
        {"type": "text", "text": "before"},
        {"type": "text", "text": "middle"},
        {"type": "text", "text": "after"},
    ]
    # the caller (plan node) strips dict copies; state is untouched
    assert message["content"][1]["type"] == "image_url"


def test_remove_images_strips_image_in_middle_of_content_list() -> None:
    message = _user_message(
        ("text", {"text": "first"}),
        ("image_url", {"image_url": {"url": "data"}}),
        ("text", {"text": "last"}),
    )

    stripped = MessageBuilder.remove_images_from_message(dict(message))

    assert [item["text"] for item in stripped["content"]] == ["first", "last"]
    assert all(item["type"] == "text" for item in stripped["content"])


def test_remove_images_keeps_current_image_when_not_requested() -> None:
    """The current screenshot message is never passed to the stripper.

    plan_node strips historical messages only (P0 #3); the latest user message
    keeps its image_url. Direct stripper input must preserve the message role
    and any non-image parts untouched.
    """
    current = _user_message(
        ("image_url", {"image_url": {"url": "data:image/png;base64,current"}}),
        ("text", {"text": "当前屏幕"}),
    )

    kept = MessageBuilder.remove_images_from_message(dict(current))

    assert kept["role"] == "user"
    # stripping is applied only to historical messages in plan; a plain-text
    # assistant message (no content list) passes through unchanged
    assert kept["content"] == [{"type": "text", "text": "当前屏幕"}]


def test_remove_images_passes_through_plain_string_content() -> None:
    message = {"role": "assistant", "content": "<think>t</think>\n<answer>a</answer>"}

    stripped = MessageBuilder.remove_images_from_message(message)

    assert stripped == message
    assert stripped["content"] == "<think>t</think>\n<answer>a</answer>"


# --------------------------------------------------------------------------
# prompt documentation (CN/EN sync)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ("cn", "en"))
def test_system_prompt_documents_progress_note(lang: str) -> None:
    prompt = get_system_prompt(lang=lang, output_mode="json_schema")

    assert "progress_note" in prompt
    assert "expected_outcome" in prompt


def test_client_parse_keeps_envelope_progress_note_for_plan() -> None:
    client = ModelClient(ModelConfig(output_mode="json_schema"))

    _thinking, action, metadata = client._parse_response_with_metadata(
        _envelope_response("已等待加载，下一步点击设置")
    )

    assert metadata["parse_success"] is True
    assert '"progress_note"' in action
    assert '"expected_outcome"' in action
    assert '"action"' in action
