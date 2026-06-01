from phone_agent.graph.nodes.execute import execute_node


def test_execute_uses_config_device_factory_for_dispatch(
    base_state, fake_device
) -> None:
    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert fake_device.calls[-1] == ("tap", (500, 1000, "device-1"), {})
    assert result["messages"][-1]["role"] == "assistant"
    assert result["action_outcome_summary"]["action"] == "Tap"
    assert all(
        item.get("type") != "image_url" for item in result["messages"][0]["content"]
    )


def test_execute_sensitive_tap_sets_pending_confirm_without_dispatch(
    base_state, fake_device
) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
        "message": "支付确认",
    }

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["pending_interrupt"] == "confirmation"
    assert result["pending_execute"] is True
    assert result["context_mode"] == "observe"
    assert fake_device.calls == []


def test_pending_execute_dispatches_without_duplicate_strip_append(
    base_state, fake_device
) -> None:
    original_messages = [{"role": "assistant", "content": "already appended"}]
    base_state["messages"] = list(original_messages)
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = True

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["messages"] == original_messages
    assert result["pending_execute"] is False
    assert result["action_confirmed"] is True
    assert fake_device.calls[-1][0] == "tap"


def test_pending_execute_requires_accepted_confirmation(base_state, fake_device) -> None:
    base_state["pending_execute"] = True
    base_state["interrupt_result"] = None

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["action_confirmed"] is False
    assert fake_device.calls == []


def test_execute_takeover_sets_interrupt(base_state) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Take_over",
        "message": "验证码",
    }

    result = execute_node(base_state, {"configurable": {"verbose": False}})

    assert result["pending_interrupt"] == "takeover"
    assert result["interrupt_message"] == "验证码"


def test_execute_finish_ends_and_appends_message(base_state) -> None:
    base_state["action_parsed"] = {"_metadata": "finish", "message": "done"}
    base_state["action_raw"] = 'finish(message="done")'

    result = execute_node(base_state, {"configurable": {"verbose": False}})

    assert result["finished"] is True
    assert result["action_result"]["message"] == "done"
    assert result["messages"][-1]["role"] == "assistant"
