"""P4 continuity: progress_note intent chaining + think recovery.

Covers:
- progress_note: model envelope output -> state -> next-round plan prompt
  (sanitized + bounded, envelope top-level not stripped)
- think recovery: reasoning_content accumulated into assistant history with
  real <think>...</think> wrapper; no-reasoning providers keep the historical
  <think...>...</think...> placeholder byte-for-byte
- messages_reducer append/replace semantics unchanged by the new formats
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
from phone_agent.graph.state import messages_reducer
from phone_agent.model.client import ModelClient, ModelConfig


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
# think recovery: history wrapper + zero change without reasoning
# --------------------------------------------------------------------------


def test_strip_and_append_wraps_real_think_in_tags() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "old"},
                {"type": "image_url", "image_url": {"url": "data"}},
            ],
        }
    ]

    out = _strip_and_append(messages, "先思考再行动", '{"type":"do","action":"back"}')

    assert out[-1]["role"] == "assistant"
    assert (
        out[-1]["content"]
        == "<think>先思考再行动</think>\n<answer>{\"type\":\"do\",\"action\":\"back\"}</answer>"
    )
    assert out[0]["content"] == [{"type": "text", "text": "old"}]


def test_strip_and_append_empty_thinking_keeps_placeholder_byte_identical() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "old"}]}
    ]

    out = _strip_and_append(messages, "", '{"type":"do","action":"back"}')

    assert (
        out[-1]["content"]
        == "<think...></think...>\n<answer>{\"type\":\"do\",\"action\":\"back\"}</answer>"
    )


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
# messages_reducer compatibility with the new formats (P0 #6)
# --------------------------------------------------------------------------


def test_messages_reducer_replace_survives_real_think_wrapper() -> None:
    existing = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
    ]
    rebuilt = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old stripped"},
        {
            "role": "assistant",
            "content": "<think>真实思考</think>\n<answer>{\"type\":\"do\",\"action\":\"back\"}</answer>",
        },
    ]

    assert messages_reducer(existing, rebuilt) == rebuilt
    assert messages_reducer(existing, rebuilt) is rebuilt


def test_messages_reducer_replace_survives_empty_think_placeholder() -> None:
    existing = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
    ]
    rebuilt = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old stripped"},
        {
            "role": "assistant",
            "content": "<think...></think...>\n<answer>{\"type\":\"do\",\"action\":\"back\"}</answer>",
        },
    ]

    assert messages_reducer(existing, rebuilt) == rebuilt


def test_messages_reducer_append_survives_progress_note_user_line() -> None:
    existing = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
    ]
    new_plan_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "任务：x\n\n上轮意图：已完成上一步"}],
        }
    ]

    assert messages_reducer(existing, new_plan_messages) == existing + new_plan_messages


def test_messages_reducer_append_survives_image_content_list() -> None:
    existing = [{"role": "user", "content": [{"type": "text", "text": "old"}]}]
    new = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data"}},
                {"type": "text", "text": "任务：x\n\n上轮意图：y"},
            ],
        }
    ]

    assert messages_reducer(existing, new) == existing + new


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
