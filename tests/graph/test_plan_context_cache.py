"""P-E: plan context true three-stage (prefix-cache friendly).

Deterministic unit tests only — no model-judgment assertions. The plan-node
tests use a *recording* model client (like the existing suite's FakeModel) but
only ever assert the deterministic request *structure* (roles, markers, image
placement, char/token estimates); the canned model response never feeds an
assertion, so nothing here is a "preset output → assert decision" test.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from phone_agent.graph.context import (
    DEFAULT_CONTEXT_BUDGET,
    ContextSelectionResult,
    build_skinny_trajectory_line,
    compact_messages_for_request,
    is_fat_tail_message,
    replace_fat_tails_with_skinny,
)
from phone_agent.graph.state import messages_reducer
from phone_agent.model.client import MessageBuilder


def _selection() -> ContextSelectionResult:
    return ContextSelectionResult(
        context_mode="inject", context_strategy="inject_redacted_block"
    )


def _user_text(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant(text: str = "<think></think>\n<answer>back</answer>") -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _tail(step: int, marks: str = "** Screen Marks **\n- m1: role=Button") -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"** Screen Info **\n{{\"current_app\": \"App\"}}\n\n{marks}",
            }
        ],
    }


def _texts(messages: list[dict[str, Any]]) -> str:
    """Flatten all text parts of messages (handles string and list content)."""
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# skinny trajectory line: format, cap, sanitization, pending label
# ---------------------------------------------------------------------------


def test_skinny_line_format_action_and_target() -> None:
    state = {
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        "action_result": {"success": True, "message": "ok"},
    }
    assert build_skinny_trajectory_line(state, step_index=0) == "s0: Tap @500,500 → ok"


def test_skinny_line_uses_text_target_and_result_override() -> None:
    state = {
        "action_parsed": {"_metadata": "do", "action": "Type", "text": "搜索关键词"},
        "action_result": None,
    }
    line = build_skinny_trajectory_line(
        state,
        step_index=7,
        result={"success": False, "message": "type failed"},
    )
    assert line.startswith("s7: Type 搜索关键词 → type failed")


def test_skinny_line_hard_caps_length() -> None:
    state = {
        "action_parsed": {
            "_metadata": "do",
            "action": "Type",
            "text": "x" * 500,
        },
        "action_result": {"success": False, "message": "e" * 500},
    }
    line = build_skinny_trajectory_line(state, step_index=1, max_chars=200)
    assert len(line) <= 200
    assert line.startswith("s1: Type ")


def test_skinny_line_redacts_sensitive_values() -> None:
    state = {
        "action_parsed": {
            "_metadata": "do",
            "action": "Type",
            "text": "给 13800138000 发短信",
        },
        "action_result": {"success": False, "message": "失败 13800138000"},
    }
    line = build_skinny_trajectory_line(state, step_index=3)
    assert "13800138000" not in line
    assert line.startswith("s3: Type ")


def test_skinny_line_pending_label_both_langs() -> None:
    state = {
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [1, 2]},
        "action_result": None,
    }
    cn = build_skinny_trajectory_line(
        state,
        step_index=4,
        result={"success": None, "message": "待确认"},
        lang="cn",
    )
    en = build_skinny_trajectory_line(
        state,
        step_index=4,
        result={"success": None, "message": "awaiting confirmation"},
        lang="en",
    )
    assert cn.endswith("→ 待确认")
    assert en.endswith("→ awaiting confirmation")


# ---------------------------------------------------------------------------
# fat-tail detection and history replacement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "** Screen Info **\n{\"current_app\": \"x\"}",
        "** Screen Marks **\n- m1: role=Button",
        "** 屏幕标记（使用 target_mark_id，不要猜坐标） **",
        "** Screen Objects **\n- o1",
        "** 屏幕对象 **",
    ],
)
def test_is_fat_tail_detects_observation_markers(text: str) -> None:
    assert is_fat_tail_message(_user_text(text)) is True


def test_is_fat_tail_ignores_prefix_and_trajectory_rows() -> None:
    assert is_fat_tail_message(_user_text("测试任务")) is False
    assert is_fat_tail_message(_user_text("s0: Tap @500,500 → ok")) is False
    assert is_fat_tail_message(_assistant()) is False


def test_replace_fat_tails_keeps_prefix_and_skinnifies_history() -> None:
    history = [
        _user_text("测试任务"),
        _tail(0),
        _assistant(),
        _tail(1, marks="** 屏幕标记 **\n- ax_2: role=Button"),
        _user_text("s0: Tap → ok"),
    ]
    state = {
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [1, 2]},
        "action_result": {"success": True, "message": "ok"},
        "lang": "cn",
    }
    rebuilt, replaced = replace_fat_tails_with_skinny(history, state)
    assert replaced == 2
    assert rebuilt[0]["content"][0]["text"] == "测试任务"
    assert rebuilt[1]["content"][0]["text"] == "s0: Tap @1,2 → ok"
    assert rebuilt[2] is history[2]
    # step index follows the assistant rows that precede each tail
    assert rebuilt[3]["content"][0]["text"] == "s1: Tap @1,2 → ok"
    assert rebuilt[4] is history[4]
    joined = _texts(rebuilt)
    assert "** Screen Info **" not in joined
    assert "屏幕标记" not in joined


# ---------------------------------------------------------------------------
# window deletion + P0 #3 (images only in the latest user message)
# ---------------------------------------------------------------------------


def test_compact_keeps_every_history_row_window_deleted() -> None:
    history = [
        MessageBuilder.create_user_message(
            text=f"** Screen Info **\nstep-{i}",
            image_base64=f"old-{i}" if i % 2 == 0 else None,
        )
        for i in range(20)
    ]
    history.append(
        MessageBuilder.create_user_message(text="current", image_base64="current")
    )
    compacted, _ = compact_messages_for_request(history, _selection())
    assert len(compacted) == len(history)
    image_holders = [
        index
        for index, message in enumerate(compacted)
        if isinstance(message.get("content"), list)
        and any(item.get("type") == "image_url" for item in message["content"])
    ]
    assert image_holders == [len(history) - 1]


def test_compact_strips_historical_images_but_keeps_latest_user_image() -> None:
    messages = [
        MessageBuilder.create_user_message(text="old", image_base64="old"),
        _assistant(),
        MessageBuilder.create_user_message(text="current", image_base64="current"),
    ]
    compacted, _ = compact_messages_for_request(messages, _selection())
    assert compacted[0]["content"] == [{"type": "text", "text": "old"}]
    assert "image_url" in [item["type"] for item in compacted[2]["content"]]


# ---------------------------------------------------------------------------
# reducer semantics: plan appends, execute replaces (P0 #6)
# ---------------------------------------------------------------------------


def test_reducer_plan_append_execute_replace_semantics() -> None:
    existing = [
        {"role": "system", "content": "sys"},
        _user_text("task"),
    ]
    plan_new = [_tail(0)]
    appended = messages_reducer(existing, plan_new)
    assert appended == existing + plan_new

    rebuilt = existing + [_user_text("s0: Tap → ok"), _assistant()]
    assert messages_reducer(existing + plan_new, rebuilt) == rebuilt


# ---------------------------------------------------------------------------
# plan node request structure (recording client; structure-only assertions)
# ---------------------------------------------------------------------------


class _RecordingModel:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] | None = None
        self.calls = 0

    def request(self, messages, **kwargs):
        self.messages = messages
        self.calls += 1
        return type(
            "Resp",
            (),
            {
                "thinking": "",
                "content": '{"type":"do","action":"Back"}',
                "action": '{"type":"do","action":"Back"}',
                "parse_metadata": {},
            },
        )()


def test_plan_step0_request_has_pinned_prefix(base_state, fake_device) -> None:
    base_state["messages"] = []
    base_state["step_count"] = 0
    model = _RecordingModel()
    plan_node_result = _call_plan(base_state, fake_device, model)

    request = model.messages
    assert [message["role"] for message in request] == [
        "system",
        "user",
        "user",
        "user",
    ]
    # prefix = system + contract + task; the contract block sits right after
    # system and the task text right after it — both static across steps.
    assert "任务目标契约" in request[1]["content"][-1]["text"]
    assert request[2]["content"][-1]["text"] == base_state["task"]
    # current tail = screen info + marks + context + screenshot
    tail_text = request[3]["content"][-1]["text"]
    assert "** Screen Info **" in tail_text
    assert any(item.get("type") == "image_url" for item in request[3]["content"])
    # plan returns only its new messages (append semantics)
    assert [message["role"] for message in plan_node_result["messages"]] == [
        "system",
        "user",
        "user",
        "user",
    ]


def test_plan_step1_history_is_lean_trajectory(base_state, fake_device) -> None:
    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        _user_text("** 任务目标契约 **\ncriterion=1"),
        _user_text("测试任务"),
        _user_text("s0: Tap @500,500 → ok"),
        _assistant(),
    ]
    model = _RecordingModel()
    _call_plan(base_state, fake_device, model)

    request = model.messages
    assert [message["role"] for message in request] == [
        "system",
        "user",
        "user",
        "user",
        "assistant",
        "user",
    ]
    joined_history = _texts(request[:-1])
    # no marks/screen-info text survives in history (fat tails are replaced)
    assert "** Screen Info **" not in joined_history
    assert "** Screen Marks" not in joined_history
    # contract appears exactly once (prefix), never re-injected per step
    assert joined_history.count("任务目标契约") == 1
    # the current tail still carries the screenshot
    assert any(
        item.get("type") == "image_url" for item in request[-1]["content"]
    )


def test_plan_defense_replaces_stale_fat_tail_in_request(base_state, fake_device) -> None:
    # a fat tail survived in state (e.g. a confirm-reject flow); the plan-side
    # safety net must keep it out of the request without touching the prefix.
    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        _user_text("** 任务目标契约 **\ncriterion=1"),
        _user_text("测试任务"),
        _tail(0, marks="** Screen Marks **\n- ax_1: role=Button"),
        _assistant(),
    ]
    model = _RecordingModel()
    _call_plan(base_state, fake_device, model)

    request = model.messages
    # history only: the current tail legitimately carries its own Screen Info
    joined = _texts(request[:-1])
    assert "** Screen Marks" not in joined
    assert "** Screen Info **" not in joined
    # the stale tail was replaced by its trajectory row
    assert "s0:" in joined


def test_plan_stepN_returns_only_new_tail(base_state, fake_device) -> None:
    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        _user_text("** 任务目标契约 **\ncriterion=1"),
        _user_text("测试任务"),
        _user_text("s0: Tap → ok"),
        _assistant(),
    ]
    model = _RecordingModel()
    result = _call_plan(base_state, fake_device, model)

    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "user"
    assert "** Screen Info **" in result["messages"][0]["content"][-1]["text"]


def _recompile_contract(criterion_name: str) -> Any:
    from phone_agent.graph.goal import CriterionSpec, GoalContract

    return GoalContract(
        task_hash="recompile-test",
        redacted_objective="打开设置页",
        objective_length=5,
        compile_status="compiled",
        success_criteria=[
            CriterionSpec(
                name=criterion_name,
                description=f"新判据：{criterion_name}",
                verification="app_or_activity_match",
                required=True,
            )
        ],
    )


def test_recompile_refreshes_contract_block_still_once(
    base_state, fake_device
) -> None:
    """F5: after a goal recompile the contract block is replaced in place by
    the fresh contract — still exactly once in history (never duplicated), but
    with the new criterion text the model must now see."""
    from phone_agent.graph.nodes import goal_node

    old_contract = _recompile_contract("旧判据")
    new_contract = _recompile_contract("新判据")
    state = {
        **base_state,
        "lang": "cn",
        "needs_recompile": True,
        "goal_contract": old_contract,
        "goal_contract_status": "compiled",
        "messages": [
            {"role": "system", "content": "sys"},
            _user_text(old_contract.to_prompt_block(lang="cn")),
            _user_text("测试任务"),
            _user_text("s0: Tap → ok"),
            _assistant(),
        ],
    }

    refreshed = goal_node._refresh_contract_message(
        state, new_contract, lang="cn"
    )

    assert "messages" in refreshed
    messages = refreshed["messages"]
    joined = _texts(messages)
    # still exactly once (the replace never duplicates the prefix block)
    assert joined.count("任务目标契约") == 1
    assert "旧判据" not in joined
    assert "新判据" in joined
    contract_msg = next(
        message
        for message in messages
        if message.get("role") == "user"
        and "任务目标契约" in _texts([message])
    )
    assert contract_msg["content"] == [
        {"type": "text", "text": new_contract.to_prompt_block(lang="cn")}
    ]


def test_recompile_without_contract_block_leaves_messages_untouched(
    base_state,
) -> None:
    """F5: when no contract block exists in history (first compile / resume
    paths), a recompile must not fabricate or touch messages."""
    from phone_agent.graph.nodes import goal_node

    state = {
        **base_state,
        "lang": "cn",
        "needs_recompile": True,
        "messages": [
            {"role": "system", "content": "sys"},
            _user_text("测试任务"),
            _user_text("s0: Tap → ok"),
        ],
    }
    refreshed = goal_node._refresh_contract_message(
        state, _recompile_contract("新判据"), lang="cn"
    )
    assert refreshed == {}


def test_recompile_refreshed_messages_survive_reducer_replace(
    base_state,
) -> None:
    """F5: the rebuilt list returned by goal_node is a full replace (P0 #6),
    so the reducer swaps the whole channel instead of appending a duplicate
    contract block."""
    from phone_agent.graph.nodes import goal_node

    old_contract = _recompile_contract("旧判据")
    new_contract = _recompile_contract("新判据")
    existing = [
        {"role": "system", "content": "sys"},
        _user_text(old_contract.to_prompt_block(lang="cn")),
        _user_text("测试任务"),
    ]
    refreshed = goal_node._refresh_contract_message(
        {**base_state, "messages": existing, "lang": "cn"},
        new_contract,
        lang="cn",
    )
    rebuilt = refreshed["messages"]
    reduced = messages_reducer(existing, rebuilt)
    assert reduced == rebuilt
    assert _texts(reduced).count("任务目标契约") == 1


# ---------------------------------------------------------------------------
# F10: historical assistant think blocks are stripped on the execute rebuild
# ---------------------------------------------------------------------------


def _think_answer(step: int, think_len: int = 300) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": (
            f"<think>{'t' * think_len}</think>\n"
            f"<answer>{{\"action\":\"Tap\",\"step\":{step}}}</answer>"
        ),
    }


def test_execute_rebuild_strips_historical_think_blocks(
    base_state, fake_device
) -> None:
    """F10: on the execute full-rebuild path every historical assistant
    message loses its <think> section (only the newest keeps it), so 20 steps
    of thinking stay bounded; the answer text is preserved byte-for-byte."""
    from phone_agent.graph.nodes.execute import execute_node

    history: list[dict[str, Any]] = []
    for step in range(20):
        history.append(_user_text(f"s{step}: Tap @{step},1 → ok"))
        history.append(_think_answer(step))
    base_state["messages"] = history
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
    }
    base_state["thinking"] = "current-think"
    base_state["action_raw"] = '{"action":"Tap","step":99}'
    base_state["step_count"] = 20

    result = execute_node(
        base_state,
        {"configurable": {"device_factory": fake_device, "verbose": False}},
    )

    messages = result["messages"]
    assistants = [
        message for message in messages if message.get("role") == "assistant"
    ]
    assert len(assistants) == 21  # 20 historical + 1 appended this step
    # every historical assistant is reduced to its answer
    for message in assistants[:-1]:
        assert "<think>" not in message["content"]
        assert "<answer>" in message["content"]
        assert message["content"].count("<answer>") == 1
    # the newest assistant keeps its think block
    assert "<think>current-think</think>" in assistants[-1]["content"]
    # answer text is byte-for-byte unchanged
    assert assistants[-1]["content"].endswith(
        '<answer>{"action":"Tap","step":99}</answer>'
    )
    assert '{"action":"Tap","step":0}' in assistants[0]["content"]
    # bounded: 20 * 300-char thinks would be ~6000 chars; stripped history is small
    total = sum(len(message["content"]) for message in assistants[:-1])
    assert total < 1500


def test_strip_think_block_preserves_answer_and_placeholder_format() -> None:
    """F10 unit: the form-level strip handles both the real <think> form and
    the historical <think...> placeholder, and leaves answer bytes untouched."""
    from phone_agent.graph.nodes.execute import _strip_think_block

    real = "<think>reasoning here</think>\n<answer>payload</answer>"
    assert _strip_think_block(real) == "\n<answer>payload</answer>"

    placeholder = "<think...>reasoning</think...>\n<answer>payload</answer>"
    assert _strip_think_block(placeholder) == "\n<answer>payload</answer>"

    no_think = "\n<answer>payload</answer>"
    assert _strip_think_block(no_think) == no_think

    answer_only = "<answer>contains <think> inside</answer>"
    # no closing tag after the opening -> untouched
    assert _strip_think_block(answer_only) == answer_only


def _call_plan(base_state, fake_device, model) -> dict[str, Any]:
    from phone_agent.graph.nodes.plan import plan_node

    return plan_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
            }
        },
    )


# ---------------------------------------------------------------------------
# execute full rebuild: old fat tail becomes a skinny row (P0 #6 replace)
# ---------------------------------------------------------------------------


def test_execute_rebuild_skinnifies_old_fat_tail(base_state, fake_device) -> None:
    from phone_agent.graph.nodes.execute import execute_node

    base_state["messages"] = [
        {"role": "system", "content": "sys"},
        _user_text("** 任务目标契约 **\ncriterion=1"),
        _user_text("测试任务"),
        _tail(0, marks="** Screen Marks **\n- ax_1: role=Button"),
    ]
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
    }
    base_state["action_raw"] = '{"type":"do","action":"Tap","element":[500,500]}'
    base_state["thinking"] = "thought"
    base_state["lang"] = "cn"
    base_state["step_count"] = 1

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    messages = result["messages"]
    assert messages[-1]["role"] == "assistant"
    replaced_user = messages[3]
    assert replaced_user["role"] == "user"
    row = replaced_user["content"][0]["text"]
    assert re.match(r"^s0: Tap @500,500 → ok$", row)
    joined = _texts(messages)
    assert "** Screen Marks" not in joined
    assert "** Screen Info **" not in joined


# ---------------------------------------------------------------------------
# 20+ step synthetic session: token estimate comparison
# ---------------------------------------------------------------------------


def _marks_block() -> str:
    # realistic marks block, ~7k chars like the task-doc measurement
    return "\n".join(
        [
            "** Screen Marks (use target_mark_id; do not guess coordinates) **",
            *[
                f"- m_{i}: role=Button source=accessibility confidence=0.99 "
                f"bbox=[{i},0,{i + 10},80] center=[{i + 5},40] position=top "
                f"text_summary=item_{i}"
                for i in range(200)
            ],
        ]
    )


def _fat_step_messages(step: int) -> list[dict[str, Any]]:
    return [
        MessageBuilder.create_user_message(
            text=f"任务：t\n\n** Screen Info **\n{{}}\n\n{_marks_block()}",
            image_base64=f"img-{step}",
        ),
        _assistant(f"<think></think>\n<answer>{{\"action\":\"Tap\"}}</answer>"),
    ]


def _skinny_step_messages(step: int) -> list[dict[str, Any]]:
    return [
        MessageBuilder.create_user_message(
            text=f"s{step}: Tap @{step},1 → ok", image_base64=None
        ),
        _assistant(f"<think></think>\n<answer>{{\"action\":\"Tap\"}}</answer>"),
    ]


def test_20_step_synthetic_session_token_estimate_drops_dramatically() -> None:
    prefix = [
        {"role": "system", "content": "sys"},
        _user_text("** 任务目标契约 **\ncriterion=1"),
        _user_text("测试任务"),
    ]
    fat_history: list[dict[str, Any]] = []
    skinny_history: list[dict[str, Any]] = []
    for step in range(20):
        fat_history.extend(_fat_step_messages(step))
        skinny_history.extend(_skinny_step_messages(step))
    current = MessageBuilder.create_user_message(
        text="** Screen Info **\ncurrent", image_base64="current"
    )
    fat_request = prefix + fat_history + [current]
    skinny_request = prefix + skinny_history + [current]

    fat_compacted, fat_selection = compact_messages_for_request(
        fat_request, _selection()
    )
    skinny_compacted, skinny_selection = compact_messages_for_request(
        skinny_request, _selection()
    )

    # window deleted: nothing is dropped in either form
    assert len(fat_compacted) == len(fat_request)
    assert len(skinny_compacted) == len(skinny_request)
    assert fat_selection.messages_after == len(fat_request)
    assert skinny_selection.messages_after == len(skinny_request)

    fat_chars = fat_selection.message_chars_after
    skinny_chars = skinny_selection.message_chars_after
    fat_tokens = fat_selection.approx_tokens_after
    skinny_tokens = skinny_selection.approx_tokens_after

    assert fat_chars > 30_000
    assert skinny_chars < 10_000
    assert skinny_chars < fat_chars / 4
    assert skinny_tokens < fat_tokens / 4

    # the current tail keeps its image in both forms (P0 #3 regression)
    assert any(
        item.get("type") == "image_url" for item in fat_compacted[-1]["content"]
    )
    assert any(
        item.get("type") == "image_url" for item in skinny_compacted[-1]["content"]
    )
    # no historical image survives either form
    for message in fat_compacted[:-1] + skinny_compacted[:-1]:
        content = message.get("content")
        if isinstance(content, list):
            assert all(item.get("type") != "image_url" for item in content)


# ---------------------------------------------------------------------------
# sliding-window artifacts are gone
# ---------------------------------------------------------------------------


def test_request_recent_messages_and_bound_function_removed() -> None:
    assert "request_recent_messages" not in DEFAULT_CONTEXT_BUDGET
    import phone_agent.graph.context as context_module

    assert not hasattr(context_module, "_bound_request_messages")
    assert "request_recent_messages" not in context_module.__dict__.get(
        "DEFAULT_CONTEXT_BUDGET", {}
    )
