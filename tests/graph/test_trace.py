import json

from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.trace import JsonlTraceWriter, sanitize_for_trace


def test_jsonl_trace_writer_writes_redacted_events(tmp_path) -> None:
    writer = JsonlTraceWriter(trace_id="trace-1", trace_dir=tmp_path)

    writer.emit(
        "plan",
        "plan_result",
        1,
        {"task": "打开隐私页面", "screenshot_b64": "raw-image", "api_key": "secret"},
    )

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["trace_id"] == "trace-1"
    assert record["run_id"] == "trace-1"
    assert record["step_id"] == 1
    assert record["node"] == "plan"
    assert record["event"] == "plan_result"
    assert record["payload"]["task"]["redacted"] is True
    assert record["payload"]["screenshot_b64"] == "<redacted>"
    assert record["payload"]["api_key"] == "<redacted>"


def test_sanitize_preserves_non_sensitive_shape() -> None:
    payload = {"action": "Tap", "result": {"success": True}, "items": [(1, 2)]}

    assert sanitize_for_trace(payload) == {
        "action": "Tap",
        "result": {"success": True},
        "items": [[1, 2]],
    }


def test_sanitize_redacts_identifiable_visible_text() -> None:
    payload = {"screen_belief": {"visible_text": "张三 13800138000 订单123456"}}

    sanitized = sanitize_for_trace(payload)

    raw = json.dumps(sanitized, ensure_ascii=False)
    assert "张三" not in raw
    assert "13800138000" not in raw
    assert "订单123456" not in raw
    assert sanitized["screen_belief"]["visible_text"]["redacted"] is True


def test_sanitize_redacts_parse_error_text() -> None:
    payload = {"parse_error": "Failed raw action 13800138000"}

    sanitized = sanitize_for_trace(payload)

    assert "13800138000" not in json.dumps(sanitized, ensure_ascii=False)
    assert sanitized["parse_error"]["redacted"] is True


def test_sanitize_keeps_context_metrics_but_not_raw_context_block() -> None:
    payload = {
        "prompt_version": "context_harness_v1",
        "context_strategy": "inject_redacted_block",
        "selected_sections": ["screen_belief", "failure_memory"],
        "messages_before": 4,
        "messages_after": 4,
        "context_block": "张三 13800138000",
    }

    sanitized = sanitize_for_trace(payload)

    assert sanitized["prompt_version"] == "context_harness_v1"
    assert sanitized["selected_sections"] == ["screen_belief", "failure_memory"]
    assert sanitized["messages_before"] == 4
    assert "张三" not in json.dumps(sanitized, ensure_ascii=False)
    assert sanitized["context_block"]["redacted"] is True


def test_sanitize_redacts_grounding_target_hint_but_keeps_hashes() -> None:
    payload = {
        "grounding_observation": {
            "provider": "fake",
            "raw_screenshot_hash": "hash-1",
            "provider_input_hash": "hash-2",
            "target": {"target_text_hint": "张三 13800138000"},
        }
    }

    sanitized = sanitize_for_trace(payload)

    raw = json.dumps(sanitized, ensure_ascii=False)
    assert "13800138000" not in raw
    assert sanitized["grounding_observation"]["raw_screenshot_hash"] == "hash-1"
    assert sanitized["grounding_observation"]["provider_input_hash"] == "hash-2"
    assert sanitized["grounding_observation"]["target"]["target_text_hint"]["redacted"] is True


def test_execute_trace_records_confirm_interrupt(base_state, tmp_path) -> None:
    writer = JsonlTraceWriter(trace_id="trace-confirm", trace_dir=tmp_path)
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
        "message": "支付确认",
    }

    result = execute_node(
        base_state,
        {"configurable": {"trace_writer": writer, "verbose": False}},
    )

    records = [
        json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()
    ]
    assert result["pending_interrupt"] == "confirmation"
    assert any(item["event"] == "safety_decision" for item in records)
    assert any(item["event"] == "confirm_interrupt" for item in records)
    confirm_record = next(item for item in records if item["event"] == "confirm_interrupt")
    payload = confirm_record["payload"]
    assert payload["interrupt_message"]["redacted"] is True
