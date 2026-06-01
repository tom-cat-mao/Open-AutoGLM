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
    assert any(item["event"] == "confirm_interrupt" for item in records)
    payload = records[0]["payload"]
    assert payload["interrupt_message"]["redacted"] is True
