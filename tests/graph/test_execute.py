from phone_agent.graph.context import build_plan_context_block
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


def test_execute_repeat_rejection_counts_tried_actions_and_sets_flag(
    base_state, fake_device
) -> None:
    """1.2: a rejected repeat is a system decision — the action must still be
    counted in gui_memory.tried_actions (the guard's counting source) so the
    repeat count escalates, and repeat_rejected must route the next edge."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["repeat_rejected"] is True
    assert result["finished"] is False
    tried = result["gui_memory"]["tried_actions"]
    assert len(tried) == 3
    latest = tried[-1]
    assert latest["action"] == "Tap"
    assert latest["target_center"] == [500.0, 500.0]
    assert latest["surface"] == surface
    assert latest["failure_cause"] == "repeated_action"
    assert latest["result_success"] is False
    assert fake_device.calls == []
    # No failure_memory write: the rejection must not pollute failure memory.
    assert "failure_memory" not in result


def test_execute_repeat_rejection_escalates_count_via_tried_actions(
    base_state, fake_device
) -> None:
    """A model that keeps proposing the same target sees an escalating count:
    each rejection appends to tried_actions, which the next rejection counts."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
    ]
    base_state["failure_memory"] = [
        {"step_count": 1, "action": "Tap", "failure_cause": "wrong_page"}
    ]

    first = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )
    assert first["action_receipt"]["side_effect_receipt"]["repeat_count"] == 3

    second_state = {
        **base_state,
        "gui_memory": first["gui_memory"],
        "failure_memory": base_state["failure_memory"],
    }
    second = execute_node(
        second_state,
        {"configurable": {"device_factory": fake_device, "verbose": False}},
    )
    assert second["action_receipt"]["side_effect_receipt"]["repeat_count"] == 4


def test_execute_repeat_rejection_outcome_flows_into_context(
    base_state, fake_device
) -> None:
    """The rejection reason must reach the next plan prompt through the
    existing context mechanism (last_action_outcome / avoid_repeating)."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["grounding_observation"] = {"center": [500, 500]}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
        {"action": "Tap", "target_center": [500.0, 500.0], "surface": surface},
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    outcome = result["action_outcome_summary"]
    assert outcome["dispatch_status"] == "rejected"
    assert outcome["failure_cause"] == "repeated_action"
    assert "重复目标动作已被拒绝" in outcome["result_message_summary"]

    block, _metrics = build_plan_context_block(
        {**base_state, **result, "action_parsed": {"_metadata": "do", "action": "Tap"}}
    )
    assert "avoid_repeating" in block
    assert "repeat_count" in block
    assert "重复目标动作已被拒绝" in block


def test_execute_swipe_repeat_guard_rejects_same_gesture(
    base_state, fake_device
) -> None:
    """P3 #3: the repeat guard also covers Swipe — identical start/end gestures
    on the same surface (up to grid jitter) are rejected like repeated taps."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Swipe",
        "start": [500, 900],
        "end": [500, 300],
    }
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Swipe",
            "start": [500.0, 900.0],
            "end": [500.0, 300.0],
            "surface": surface,
        },
        {
            "action": "Swipe",
            "start": [505.0, 895.0],
            "end": [498.0, 302.0],
            "surface": surface,
        },
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["repeat_rejected"] is True
    assert result["finished"] is False
    assert result["action_receipt"]["dispatch_status"] == "rejected"
    assert fake_device.calls == []
    # The rejected swipe is still recorded (with its geometry) so the count
    # escalates on the next identical proposal.
    tried = result["gui_memory"]["tried_actions"]
    assert len(tried) == 3
    latest = tried[-1]
    assert latest["action"] == "Swipe"
    assert latest["start"] == [500.0, 900.0]
    assert latest["end"] == [500.0, 300.0]
    assert latest["failure_cause"] == "repeated_action"
    assert "failure_memory" not in result


def test_execute_swipe_repeat_guard_allows_different_start(
    base_state, fake_device
) -> None:
    """A swipe from a different start point is progress, not a repeat."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Swipe",
        "start": [800, 900],
        "end": [800, 300],
    }
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {
            "action": "Swipe",
            "start": [500.0, 900.0],
            "end": [500.0, 300.0],
            "surface": surface,
        },
        {
            "action": "Swipe",
            "start": [500.0, 900.0],
            "end": [500.0, 300.0],
            "surface": surface,
        },
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert any(call[0] == "swipe" for call in fake_device.calls)


# ----------------------------------------------------------------------
# R4: tap-class repeat key carries a 20-unit geometric fingerprint — swapping
# mark_id on the same physical button cannot escape the guard (pi-16)
# ----------------------------------------------------------------------


def test_execute_repeat_guard_rejects_same_bucket_different_mark_id(
    base_state, fake_device
) -> None:
    """R4 (pi-16): the model taps ax_41, then ax_42 (same bbox, sub-bucket
    center jitter), then ax_43 — the same physical button. The 20-unit
    geometry bucket collapses the jitter, so the third tap is rejected without
    dispatch."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [622, 913]}
    base_state["grounding_observation"] = {"center": [622, 913]}
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [622.0, 913.0], "surface": surface, "mark_id": "ax_41"},
        {"action": "Tap", "target_center": [624.0, 911.0], "surface": surface, "mark_id": "ax_42"},
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "repeated_action"
    assert result["repeat_rejected"] is True
    assert result["action_receipt"]["side_effect_receipt"]["reason_code"] == "repeated_target_loop"
    assert result["action_receipt"]["side_effect_receipt"]["repeat_count"] == 3
    assert fake_device.calls == []


def test_execute_repeat_guard_allows_different_bucket_same_mark_id(
    base_state, fake_device
) -> None:
    """R4: a target that genuinely moved out of the 20-unit bucket is a new
    key — same mark_id at a different position is progress, not a repeat."""
    surface = "com.xingin.xhs/SearchActivity"
    base_state["action_parsed"] = {"_metadata": "do", "action": "Tap", "element": [700, 913]}
    base_state["grounding_observation"] = {"center": [700, 913]}
    base_state["observation"] = {"snapshot": {"foreground_activity": surface}}
    base_state["gui_memory"]["tried_actions"] = [
        {"action": "Tap", "target_center": [622.0, 913.0], "surface": surface, "mark_id": "ax_42"},
        {"action": "Tap", "target_center": [622.0, 913.0], "surface": surface, "mark_id": "ax_42"},
    ]

    result = execute_node(
        base_state, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )

    assert result["action_result"]["success"] is True
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert fake_device.calls[-1][0] == "tap"
