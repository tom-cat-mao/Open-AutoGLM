"""Tests for v2 middleware (safety predicate, image pruning, trace redaction).

Per refactor-thin-loop-v2 §12. No real device, no MLX, no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from phone_agent.v2.middleware.images import ImagePruningMiddleware
from phone_agent.v2.middleware.safety import (
    build_hitl_middleware,
    is_sensitive_tool_call,
)
from phone_agent.v2.middleware.trace import TraceMiddleware, redact_args


# --------------------------------------------------------------------------
# 9.1 safety predicate
# --------------------------------------------------------------------------
def _request(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args})


def test_sensitive_type_text_triggers():
    assert is_sensitive_tool_call(_request("type_text", {"text": "我的支付密码是1234"}))
    assert is_sensitive_tool_call(_request("type_text", {"text": "enter payment password"}))
    assert is_sensitive_tool_call(_request("type_text", {"text": "验证码 887766"}))


def test_ordinary_type_text_not_sensitive():
    assert not is_sensitive_tool_call(_request("type_text", {"text": "北京天气"}))
    assert not is_sensitive_tool_call(_request("type_text", {"text": "hello world"}))


def test_tap_sensitive_by_description():
    assert is_sensitive_tool_call(_request("tap", {"target_description": "确认付款"}))
    assert not is_sensitive_tool_call(_request("tap", {"target_description": "返回首页"}))


def test_tap_sensitive_by_resolved_mark_text():
    mark = SimpleNamespace(text_summary="立即支付")
    session = SimpleNamespace(marks={"ax_9": mark})
    req = _request("tap", {"target_mark_id": "ax_9"})
    assert is_sensitive_tool_call(req, session)

    benign = SimpleNamespace(marks={"ax_9": SimpleNamespace(text_summary="设置")})
    assert not is_sensitive_tool_call(req, benign)


def test_launch_app_sensitive():
    assert is_sensitive_tool_call(_request("launch_app", {"app_name": "招商银行"}))
    assert is_sensitive_tool_call(_request("launch_app", {"app_name": "Alipay"}))
    assert not is_sensitive_tool_call(_request("launch_app", {"app_name": "相机"}))


def test_build_hitl_middleware_config():
    mw = build_hitl_middleware()
    assert set(mw.interrupt_on) >= {"tap", "long_press", "type_text", "launch_app", "ask_user", "take_over"}
    # ask_user is respond-only; take_over always interrupts (no when predicate).
    assert mw.interrupt_on["ask_user"]["allowed_decisions"] == ["respond"]
    assert "when" not in mw.interrupt_on["take_over"]
    # Actuation tools carry a sensitivity predicate.
    assert callable(mw.interrupt_on["tap"]["when"])


# --------------------------------------------------------------------------
# 9.2 image pruning
# --------------------------------------------------------------------------
def _image_msg(seq: int) -> HumanMessage:
    return HumanMessage(
        content=[
            {"type": "text", "text": f"screen {seq}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,AAAA{seq}"}},
        ]
    )


def _count_images(message) -> int:
    return sum(
        1
        for block in message.content
        if isinstance(block, dict) and block.get("type") in {"image_url", "image"}
    )


def test_images_pruning_keeps_only_newest():
    m1, m2, m3 = _image_msg(1), _image_msg(2), _image_msg(3)
    state = {"messages": [m1, m2, m3]}
    mw = ImagePruningMiddleware()
    result = mw.before_model(state, runtime=None)

    assert result is not None
    # After pruning: only the newest message still carries an image block.
    assert _count_images(m1) == 0
    assert _count_images(m2) == 0
    assert _count_images(m3) == 1
    # Placeholders reference the pruned screen numbers.
    placeholders = [
        b["text"] for b in m1.content if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any("已剪除" in text for text in placeholders)


def test_images_pruning_noop_with_single_image():
    m = _image_msg(1)
    mw = ImagePruningMiddleware()
    assert mw.before_model({"messages": [m]}, runtime=None) is None
    assert _count_images(m) == 1


# --------------------------------------------------------------------------
# 9.3 trace redaction
# --------------------------------------------------------------------------
def test_trace_redacts_and_truncates(tmp_path):
    mw = TraceMiddleware("run-redact", trace_dir=str(tmp_path), enabled=True)
    long_text = "北京今天天气不错适合出门散步 " * 20
    args = {
        "text": "支付密码 sk-ABCDEF1234567890",
        "note": long_text,
        "screenshot": {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "Q" * 400}},
    }
    redacted = redact_args(args)

    # Long value truncated to <= 64 chars + ellipsis.
    assert len(redacted["note"]) <= 65
    assert redacted["note"].endswith("…")
    # Sensitive key material redacted.
    assert "<redacted>" in redacted["text"]
    assert "sk-ABCDEF1234567890" not in redacted["text"]
    # Image base64 never logged; only bytes + type recorded.
    assert redacted["screenshot"]["type"] == "image"
    assert "url" not in redacted["screenshot"]
    assert redacted["screenshot"]["bytes"] > 0

    # And the wrap_tool_call path writes a redacted JSONL event.
    req = SimpleNamespace(tool_call={"name": "type_text", "args": {"text": "验证码 998877"}})
    mw.wrap_tool_call(req, handler=lambda r: SimpleNamespace(content="OK"))
    lines = (tmp_path / "run-redact.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert tool_calls
    logged = json.dumps(tool_calls[-1], ensure_ascii=False)
    assert "998877" not in logged
    assert "<redacted>" in logged


def test_trace_disabled_writes_nothing(tmp_path):
    mw = TraceMiddleware("run-off", trace_dir=str(tmp_path), enabled=False)
    assert mw.trace_path is None
    mw.wrap_tool_call(
        SimpleNamespace(tool_call={"name": "tap", "args": {}}),
        handler=lambda r: SimpleNamespace(content="OK"),
    )
    assert not list(tmp_path.iterdir())
