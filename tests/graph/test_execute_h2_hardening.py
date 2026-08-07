"""H2 execute-side hardening tests: Fix C (confirm reobserve), Fix E (pending
cleanup), Fix G (terminal skinnify), Fix H (answer cap).

All tests are deterministic: no FakeModel-style verdict stubs. Device doubles
only record tool calls / return fixed screenshots; safety/capability
monkeypatches change *code* decisions, never model judgments.
"""

from __future__ import annotations

from phone_agent.graph.context import (
    is_fat_tail_message,
    replace_fat_tails_with_skinny,
)
from phone_agent.graph.marks import MarkRegistry, compute_raw_screenshot_hash
from phone_agent.graph.nodes.execute import (
    ANSWER_TRUNCATION_LIMIT,
    _strip_and_append,
    _strip_think_from_history,
    execute_node,
)

FRESH_B64 = "fake-image"
STALE_B64 = "different-image"


def _registry_dict(*, raw_hash: str, screen_id: str = "screen-A") -> dict:
    return MarkRegistry(
        screen_id=screen_id,
        marks={},
        raw_screenshot_hash=raw_hash,
    ).to_dict()


def _mark_grounded_pending_state(base_state: dict, *, registry_hash: str) -> dict:
    """A resume state where confirm accepted a mark-grounded Tap."""
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
    }
    base_state["grounding_result"] = {
        "success": True,
        "provider": "mark_registry",
        "target": {"mark_id": "m1"},
    }
    base_state["mark_registry"] = _registry_dict(raw_hash=registry_hash)
    return base_state


# ---------------------------------------------------------------------------
# Fix C: confirm-accepted mark freshness check
# ---------------------------------------------------------------------------


def test_confirm_accept_fresh_mark_dispatches(base_state, fake_device) -> None:
    """Fresh frame hash == registry bound hash → normal dispatch path."""
    state = _mark_grounded_pending_state(
        base_state, registry_hash=compute_raw_screenshot_hash(FRESH_B64)
    )

    result = execute_node(
        state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_confirmed"] is True
    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert fake_device.calls[-1][0] == "tap"
    # 新鲜度校验恰好消耗一张截图（不重复抓帧）
    assert [call[0] for call in fake_device.calls].count("get_screenshot") == 1
    # messages 仍是 confirm 首轮瘦行（无二次 append）
    assert result["messages"][-1]["role"] == "user"


def test_confirm_accept_stale_mark_fails_closed_and_replans(
    base_state, fake_device
) -> None:
    """Screen changed while the user was confirming → never dispatch.

    Fail-closed: pending set cleared, routed to replan via repeat_rejected,
    the confirm row becomes the reobserve skinny line, and the unexecuted
    action produces NO gui_memory.tried_actions entry.
    """
    state = _mark_grounded_pending_state(
        base_state, registry_hash=compute_raw_screenshot_hash(STALE_B64)
    )
    state["gui_memory"] = {
        "visited_screens": [],
        "tried_actions": [],
        "scroll_memory": {},
        "task_progress": {},
    }

    result = execute_node(
        state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert not any(  # 只允许截图/前台探测调用，绝无执行调用
        call[0] not in {"get_screenshot", "get_current_app"}
        for call in fake_device.calls
    )
    assert "tap" not in [call[0] for call in fake_device.calls]
    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert result["action_confirmed"] is False
    assert result["repeat_rejected"] is True  # → after_execute "replan"
    assert result["failure_cause"] == "confirm_stale_reobserve"
    assert result["suggested_strategy"] == "reobserve"
    assert result["finished"] is False
    # 瘦行在场：sN: Tap ... → 屏幕已变化，需重新观察
    last_user = [m for m in result["messages"] if m.get("role") == "user"][-1]
    assert "屏幕已变化，需重新观察" in last_user["content"][0]["text"]
    assert last_user["content"][0]["text"].startswith("s0:")
    # 动作未执行 → 不产生 tried_actions 条目
    assert result["gui_memory"]["tried_actions"] == []


def test_confirm_accept_without_mark_skips_freshness_check(
    base_state, fake_device
) -> None:
    """Back has no mark binding → freshness check skipped, direct dispatch."""
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True
    base_state["action_parsed"] = {"_metadata": "do", "action": "Back"}
    base_state["grounding_result"] = None
    base_state["mark_registry"] = _registry_dict(raw_hash="stale-hash")

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_confirmed"] is True
    assert fake_device.calls[-1][0] == "back"
    # 无 mark 绑定 → 新鲜度校验整体跳过：不得调用 get_screenshot
    assert "get_screenshot" not in [call[0] for call in fake_device.calls]


class _BrokenScreenshotFactory:
    """Device double whose screenshot capture always fails."""

    def get_screenshot(self, device_id: str | None = None):
        raise RuntimeError("screencap failed")

    def get_current_app(self, device_id: str | None = None) -> str:
        return "FakeApp"


def test_confirm_accept_screenshot_failure_fails_closed(base_state) -> None:
    """Fresh frame capture failure → fail-closed, never dispatch."""
    state = _mark_grounded_pending_state(
        base_state, registry_hash=compute_raw_screenshot_hash(FRESH_B64)
    )

    result = execute_node(
        state,
        {
            "configurable": {
                "device_factory": _BrokenScreenshotFactory(),
                "verbose": False,
            }
        },
    )

    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert result["repeat_rejected"] is True
    assert result["failure_cause"] == "confirm_stale_reobserve"
    last_user = [m for m in result["messages"] if m.get("role") == "user"][-1]
    assert "屏幕已变化，需重新观察" in last_user["content"][0]["text"]


def test_confirm_accept_registry_without_hash_fails_closed(
    base_state, fake_device
) -> None:
    """Registry carries no raw_screenshot_hash (old checkpoint / hand-built
    state) → the freshness state is unverifiable, which is never "fresh":
    no dispatch and replan via repeat_rejected (P0 #9 fail-closed)."""
    state = _mark_grounded_pending_state(base_state, registry_hash="")

    result = execute_node(
        state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert not any(  # 只允许截图/前台探测调用，绝无执行调用
        call[0] not in {"get_screenshot", "get_current_app"}
        for call in fake_device.calls
    )
    assert "tap" not in [call[0] for call in fake_device.calls]
    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert result["action_confirmed"] is False
    assert result["repeat_rejected"] is True  # → after_execute "replan"
    assert result["failure_cause"] == "confirm_stale_reobserve"
    last_user = [m for m in result["messages"] if m.get("role") == "user"][-1]
    assert "屏幕已变化，需重新观察" in last_user["content"][0]["text"]


def test_confirm_accept_missing_device_factory_fails_closed(base_state) -> None:
    """No device_factory in configurable → freshness cannot be verified →
    fail-closed (P0 #9 spirit: unknown is never treated as fresh)."""
    state = _mark_grounded_pending_state(
        base_state, registry_hash=compute_raw_screenshot_hash(FRESH_B64)
    )

    result = execute_node(state, {"configurable": {"verbose": False}})

    assert result["pending_execute"] is False
    assert result["repeat_rejected"] is True
    assert result["failure_cause"] == "confirm_stale_reobserve"


# ---------------------------------------------------------------------------
# Fix E: pending HITL residue cleanup
# ---------------------------------------------------------------------------


def test_repeat_reject_clears_pending_hitl_fields(base_state, fake_device) -> None:
    """The repeat-reject branch returns the full pending set cleared, so a
    stale pending_execute/interrupt can never misroute a later resume."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
    ]
    base_state["pending_execute"] = True
    base_state["pending_interrupt"] = "confirmation"
    base_state["interrupt_result"] = True

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["failure_cause"] == "repeated_action"
    assert result["repeat_rejected"] is True
    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert fake_device.calls == []


def test_confirmation_required_clears_pending_hitl_fields(
    base_state, fake_device
) -> None:
    """The confirmation_required terminal branch clears the full pending set."""
    base_state["pending_execute"] = True
    base_state["pending_interrupt"] = "confirmation"
    base_state["interrupt_result"] = None

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["failure_cause"] == "confirmation_required"
    assert result["pending_execute"] is False
    assert result["pending_interrupt"] is None
    assert result["interrupt_result"] is None
    assert fake_device.calls == []


# ---------------------------------------------------------------------------
# Fix G: terminal-path trajectory gap closure
# ---------------------------------------------------------------------------


def _history_with_tail(n_steps: int) -> list[dict]:
    """n completed steps (assistant + skinny user rows) plus one fat current
    observation tail — the shape a plan frame leaves in state."""
    messages: list[dict] = []
    for step in range(n_steps):
        messages.append(
            {
                "role": "assistant",
                "content": f"<think>t</think>\n<answer>{{'step': {step}}}</answer>",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"s{step}: Tap → ok"}],
            }
        )
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "** Screen Info **\n\napp: FakeApp\nmarks: m1\n"
                    "context: 13KB of observation tail that nobody slimmed",
                },
                {"type": "image_url", "image_url": {"url": "data"}},
            ],
        }
    )
    return messages


def _last_user_text(result: dict) -> str:
    users = [m for m in result["messages"] if m.get("role") == "user"]
    return users[-1]["content"][0]["text"]


def test_terminal_validation_error_skinnifies_tail(base_state, fake_device) -> None:
    base_state["messages"] = _history_with_tail(n_steps=2)
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
        "command": "rm -rf /",
    }

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["failure_cause"] == "action_validation_failed"
    assert not any(is_fat_tail_message(m) for m in result["messages"])
    # 失败瘦行在场、编号 = 此前 assistant 行数（2）
    assert _last_user_text(result) == "s2: Tap → failed: unsafe_value"


def test_terminal_passthrough_skinnifies_tail(base_state, fake_device) -> None:
    """Plan-stage parse failure passthrough now returns slimmed messages with
    the failure row (action unknown → ``unknown``)."""
    base_state["finished"] = True
    base_state["error"] = "Model parse failed"
    base_state["failure_cause"] = "model_parse_failed"
    base_state["error_code"] = "parse_error"
    base_state["action_parsed"] = None
    base_state["action_result"] = {
        "success": False,
        "should_finish": True,
        "message": "Model parse failed",
    }
    base_state["messages"] = _history_with_tail(n_steps=1)

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert not any(is_fat_tail_message(m) for m in result["messages"])
    assert _last_user_text(result) == "s1: unknown → failed: parse_error"


def test_terminal_safety_rejected_skinnifies_tail(
    base_state, fake_device, monkeypatch
) -> None:
    import phone_agent.graph.nodes.execute as execute_module
    from phone_agent.actions.safety import SafetyDecision

    monkeypatch.setattr(
        execute_module,
        "decide_safety",
        lambda action: SafetyDecision(route="rejected", reason="test_reject"),
    )
    base_state["messages"] = _history_with_tail(n_steps=1)

    result = execute_module.execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["failure_cause"] == "action_safety_rejected"
    assert not any(is_fat_tail_message(m) for m in result["messages"])
    assert _last_user_text(result) == "s1: Tap → failed: action_safety_rejected"


def test_terminal_capability_missing_skinnifies_tail(
    base_state, fake_device, monkeypatch
) -> None:
    import phone_agent.graph.nodes.execute as execute_module

    real_capability = execute_module.get_tool_capability
    calls = {"n": 0}

    def flaky_capability(action_name: str):
        # validator.validate_action resolves get_tool_capability through its own
        # module import (the real capability), so the FIRST call that reaches
        # this monkeypatched reference is execute's own capability check —
        # returning None sends the run to the capability_missing terminal
        # branch (defense-in-depth; the branch is normally unreachable because
        # the validator already rejects missing declarations).
        calls["n"] += 1
        return None

    monkeypatch.setattr(execute_module, "get_tool_capability", flaky_capability)
    base_state["messages"] = _history_with_tail(n_steps=2)

    result = execute_module.execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["failure_cause"] == "capability_missing"
    assert not any(is_fat_tail_message(m) for m in result["messages"])
    assert _last_user_text(result) == "s2: Tap → failed: capability_missing"


def test_terminal_messages_leave_no_stale_tail_for_plan_fallback(
    base_state, fake_device
) -> None:
    """Resume scenario: after a terminal skinnify, plan's own fat-tail safety
    net finds nothing to replace — the ``sN: unknown → failed`` mis-replacement
    of stale tails can no longer trigger."""
    base_state["messages"] = _history_with_tail(n_steps=2)
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
        "command": "rm -rf /",
    }

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    rebuilt, replaced = replace_fat_tails_with_skinny(
        list(result["messages"]), base_state
    )
    assert replaced == 0
    assert rebuilt == result["messages"]


def test_non_terminal_path_message_behavior_unchanged(
    base_state, fake_device
) -> None:
    """Normal dispatch keeps appending the assistant message (no skinnify)."""
    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is False
    assert result["messages"][-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Fix H: assistant answer cap inside the strip-think channel
# ---------------------------------------------------------------------------


def _big_answer(size: int = 800) -> str:
    return '{"action":{"_metadata":"do","action":"Type","text":"' + "x" * size + '"},"parse_success":true}'


def test_strip_think_history_answer_cap_bounds_long_runs() -> None:
    """20-step synthetic session: historical assistant answers are truncated
    with the marker, the newest assistant stays byte-identical, and the total
    assistant history stays bounded."""
    messages: list[dict] = []
    big = _big_answer()
    for step in range(20):
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": f"obs {step}"}]}
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"<think>t{step}</think>\n<answer>{big}</answer>",
            }
        )
    messages.append(
        {"role": "user", "content": [{"type": "text", "text": "current obs"}]}
    )

    newest_answer = '{"action":{"_metadata":"do","action":"Back"},"parse_success":true}'
    out = _strip_and_append(
        messages,
        "fresh-thinking",
        newest_answer,
        skinny_line="s20: Back → ok",
    )

    # 最新一条 assistant 完整（think + answer 逐字节）
    latest = out[-1]
    assert latest["role"] == "assistant"
    assert latest["content"] == f"<think>fresh-thinking</think>\n<answer>{newest_answer}</answer>"
    # 历史 answer 全被截断且有界
    total_chars = 0
    for message in out[:-1]:
        if message.get("role") == "assistant":
            content = message["content"]
            assert content.endswith("…[truncated]")
            assert "x" * 800 not in content
            assert len(content) <= ANSWER_TRUNCATION_LIMIT + len("…[truncated]")
            total_chars += len(content)
    # 20 步全部截断 → 总量有界（远小于 20 × 800 的未截断量）
    assert total_chars < 20 * (ANSWER_TRUNCATION_LIMIT + 150)
    assert out[-1]["content"] == f"<think>fresh-thinking</think>\n<answer>{newest_answer}</answer>"


def test_strip_think_history_short_and_plain_answers_unchanged() -> None:
    """No think block + short answer → byte-for-byte unchanged."""
    messages = [
        {"role": "assistant", "content": "<answer>short</answer>"},
        {"role": "assistant", "content": "<think...>old placeholder</think...>\n<answer>tiny</answer>"},
    ]
    _strip_think_from_history(messages)
    assert messages[0]["content"] == "<answer>short</answer>"
    assert messages[1]["content"] == "\n<answer>tiny</answer>"


def test_strip_think_history_answer_at_limit_kept() -> None:
    """An answer whose stripped length is exactly at the limit passes through
    untruncated."""
    # stripped = "\n<answer>" + 482 chars + "</answer>" → exactly 500 chars.
    body = "y" * (ANSWER_TRUNCATION_LIMIT - len("\n<answer></answer>"))
    content = f"<think>t</think>\n<answer>{body}</answer>"
    messages = [{"role": "assistant", "content": content}]
    _strip_think_from_history(messages)
    assert "…[truncated]" not in messages[0]["content"]
    assert messages[0]["content"] == f"\n<answer>{body}</answer>"


def test_strip_think_history_non_assistant_messages_untouched() -> None:
    """System/user messages are never truncated."""
    big = _big_answer()
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": [{"type": "text", "text": big}]},
    ]
    _strip_think_from_history(messages)
    assert messages[0]["content"] == "system prompt"
    assert messages[1]["content"][0]["text"] == big
