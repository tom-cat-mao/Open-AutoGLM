from phone_agent.graph.nodes.execute import execute_node


def test_execute_uses_config_device_factory_for_dispatch(
    base_state, fake_device
) -> None:
    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert "action_succeeded" not in result
    assert result["action_ledger"][-1]["record_type"] == "action_receipt"
    assert "reflection_verdict" not in result["action_ledger"][-1]
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


def test_execute_wait_emits_receipt_without_claiming_transition(base_state, fake_device) -> None:
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
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert "action_succeeded" not in result
    assert "reflection" not in result
    assert result["action_outcome_summary"]["dispatch_status"] == "accepted"


def test_execute_unavailable_capability_fails_closed_after_safety(
    base_state, monkeypatch
) -> None:
    import phone_agent.graph.nodes.execute as execute_module

    calls: list[str] = []
    real_safety = execute_module.decide_safety
    real_capability = execute_module.get_tool_capability

    def tracked_safety(action):
        calls.append("safety")
        return real_safety(action)

    def tracked_capability(action_name):
        calls.append("capability")
        return real_capability(action_name)

    monkeypatch.setattr(execute_module, "decide_safety", tracked_safety)
    monkeypatch.setattr(execute_module, "get_tool_capability", tracked_capability)
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Call_API",
        "message": "external request",
    }

    result = execute_module.execute_node(
        base_state, {"configurable": {"verbose": False}}
    )

    assert calls.index("safety") < calls.index("capability")
    assert result["finished"] is True
    assert result["failure_cause"] == "capability_unavailable"
    assert result["action_result"]["success"] is False
    assert result["action_receipt"]["dispatch_status"] == "rejected"
    assert "action_succeeded" not in result
    assert result["action_ledger"][-1]["receipt"]["dispatch_status"] == "rejected"
    assert "goal_progress" not in result["action_ledger"][-1]


def test_execute_delegated_interact_routes_to_takeover(base_state) -> None:
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Interact",
        "message": "Complete the manual step",
    }

    result = execute_node(base_state, {"configurable": {"verbose": False}})

    assert result["pending_interrupt"] == "takeover"
    assert result["action_result"]["success"] is False
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert result["action_receipt"]["side_effect_receipt"] == {
        "delegation_status": "awaiting_acknowledgement"
    }
    assert "action_succeeded" not in result


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


def test_execute_rejects_third_same_surface_target_without_dispatch(
    base_state, fake_device
) -> None:
    surface = "com.xingin.xhs/SearchActivity"
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "repeated_action"
    assert result["action_receipt"]["dispatch_status"] == "rejected"
    assert result["action_receipt"]["side_effect_receipt"] == {
        "reason_code": "repeated_target_loop",
        "repeat_count": 3,
    }
    assert result["finished"] is False
    assert fake_device.calls == []


def test_execute_repeat_guard_distinguishes_surface_coordinate_and_type_text(
    base_state, fake_device
) -> None:
    from phone_agent.graph.context import action_text_identity

    surface = "com.xingin.xhs/SearchActivity"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Type", "text": "银石赛道"}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Type",
            "target_center": [500.0, 500.0],
            "surface": surface,
            "text_identity": action_text_identity("限速摩卡"),
        },
        {
            "action": "Type",
            "target_center": [500.0, 500.0],
            "surface": "com.xingin.xhs/ProfileActivity",
            "text_identity": action_text_identity("银石赛道"),
        },
        {
            "action": "Type",
            "target_center": [510.0, 500.0],
            "surface": surface,
            "text_identity": action_text_identity("银石赛道"),
        },
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert any(call[0] == "type_text" for call in fake_device.calls)
