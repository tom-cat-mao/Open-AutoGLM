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
    assert result["context_mode"] == "inject"
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


def test_execute_finish_records_pending_claim_and_appends_message(base_state) -> None:
    base_state["action_parsed"] = {"_metadata": "finish", "message": "done"}
    base_state["action_raw"] = '{"type":"finish","message":"done"}'

    result = execute_node(base_state, {"configurable": {"verbose": False}})

    assert result["finished"] is False
    assert result["pending_finish"] is True
    assert result["finish_validation_status"] == "pending"
    assert result["action_result"]["should_finish"] is False
    assert result["action_result"]["message"] == "done"
    assert result["messages"][-1]["role"] == "assistant"


def test_execute_appends_display_safe_action_raw(base_state, fake_device) -> None:
    private_phrase = "张三的家庭住址"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": private_phrase}
    base_state["action_raw"] = (
        '{"action":{"_metadata":"do","action":"Type",'
        '"text":{"redacted":true,"length":7,"sha256":"abc"}},"parse_success":true}'
    )

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert any(call[0] == "type_text" for call in fake_device.calls)
    assert private_phrase not in result["messages"][-1]["content"]


def test_execute_preserves_plan_parse_failure_without_dispatch(base_state, fake_device) -> None:
    base_state["finished"] = True
    base_state["error"] = "Model parse failed"
    base_state["failure_cause"] = "model_parse_failed"
    base_state["action_parsed"] = None
    base_state["action_result"] = {
        "success": False,
        "should_finish": True,
        "message": "Model parse failed",
    }

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["finished"] is True
    assert result["error"] == "Model parse failed"
    assert result["failure_cause"] == "model_parse_failed"
    assert result["action_result"]["success"] is False
    assert fake_device.calls == []


def test_execute_wait_clears_stale_reflection_wait_advice(base_state, fake_device) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Wait",
        "duration": "0.01 seconds",
    }
    base_state["reflection"] = "页面可能仍在加载"
    base_state["reflection_verdict"] = "partial"
    base_state["failure_cause"] = "network_or_loading"
    base_state["suggested_strategy"] = "wait"

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert result["reflection"] is None
    assert result["reflection_verdict"] is None
    assert result["failure_cause"] is None
    assert result["suggested_strategy"] is None
    assert result["action_succeeded"] is True
    assert result["action_outcome_summary"]["reflection_verdict"] is None
    assert result["action_outcome_summary"]["failure_cause"] is None
    assert result["action_outcome_summary"]["suggested_strategy"] is None


def test_execute_adapter_swipe_dispatches_with_existing_tool_signature(
    base_state, fake_device
) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Swipe",
        "start": [100, 100],
        "end": [900, 900],
    }

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert fake_device.calls[-1] == ("swipe", (100, 200, 900, 1800, "device-1"), {})


def test_execute_revalidates_action_and_rejects_dangerous_fields(
    base_state, fake_device
) -> None:
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
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "action_validation_failed"
    assert fake_device.calls == []
