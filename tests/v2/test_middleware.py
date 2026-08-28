"""Tests for v2 middleware (safety predicate, image pruning, trace redaction).

Per refactor-thin-loop-v2 §12. No real device, no MLX, no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from phone_agent.v2.middleware.images import (
    ImagePruningMiddleware,
    build_context_pruning_middleware,
)
from phone_agent.v2.middleware.budget import (
    BudgetMiddleware,
    build_budget_middleware,
)
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


def _obs_msg(app: str, seq: int, marks: int = 3) -> HumanMessage:
    """A ToolMessage-shaped observation: OBS text (with marks) + an image block."""

    digest = " · ".join(f"ax_{i}|Button|t{i}|(0,0)" for i in range(marks))
    return HumanMessage(
        content=[
            {"type": "text", "text": f"[OBS] app={app} screen#{seq}\nmarks ({marks}): {digest}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,IMG{seq}"}, "screen_seq": seq},
        ]
    )


def _count_images(message) -> int:
    return sum(
        1
        for block in message.content
        if isinstance(block, dict) and block.get("type") in {"image_url", "image"}
    )


def _obs_texts(message) -> list[str]:
    return [
        b["text"]
        for b in message.content
        if isinstance(b, dict) and b.get("type") == "text" and b["text"].startswith("[OBS] ")
    ]


def test_images_pruning_keeps_only_newest():
    m1, m2, m3 = _image_msg(1), _image_msg(2), _image_msg(3)
    state = {"messages": [m1, m2, m3]}
    # Legacy alias keeps only the newest image.
    mw = ImagePruningMiddleware()
    result = mw.before_model(state, runtime=None)

    assert result is not None
    assert _count_images(m1) == 0
    assert _count_images(m2) == 0
    assert _count_images(m3) == 1
    placeholders = [
        b["text"] for b in m1.content if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any("已剪除" in text for text in placeholders)


def test_images_pruning_noop_with_single_image():
    m = _image_msg(1)
    mw = ImagePruningMiddleware()
    assert mw.before_model({"messages": [m]}, runtime=None) is None
    assert _count_images(m) == 1


def test_context_pruning_keeps_newest_two_images():
    m1, m2, m3 = _image_msg(1), _image_msg(2), _image_msg(3)
    mw = build_context_pruning_middleware(keep_images=2, keep_marks=2)
    result = mw.before_model({"messages": [m1, m2, m3]}, runtime=None)

    assert result is not None
    # Newest two keep their image; the oldest is placeholdered.
    assert _count_images(m1) == 0
    assert _count_images(m2) == 1
    assert _count_images(m3) == 1
    assert any(
        isinstance(b, dict) and b.get("type") == "text" and "已剪除" in b["text"]
        for b in m1.content
    )


def test_context_pruning_noop_at_or_below_keep():
    m1, m2 = _image_msg(1), _image_msg(2)
    mw = build_context_pruning_middleware(keep_images=2, keep_marks=2)
    assert mw.before_model({"messages": [m1, m2]}, runtime=None) is None
    assert _count_images(m1) == 1
    assert _count_images(m2) == 1


def test_context_pruning_folds_old_marks_keeps_newest_two():
    msgs = [_obs_msg("app", i) for i in range(1, 5)]  # 4 OBS messages
    mw = build_context_pruning_middleware(keep_images=100, keep_marks=2)
    result = mw.before_model({"messages": msgs}, runtime=None)

    assert result is not None
    # Oldest two folded; newest two keep the full marks digest.
    assert "[marks 已折叠:3]" in _obs_texts(msgs[0])[0]
    assert "[marks 已折叠:3]" in _obs_texts(msgs[1])[0]
    assert "marks (3):" in _obs_texts(msgs[2])[0]
    assert "marks (3):" in _obs_texts(msgs[3])[0]
    # Folded lines keep the header (app/screen).
    assert _obs_texts(msgs[0])[0].startswith("[OBS] app=app screen#1")


def test_context_pruning_marks_fold_is_idempotent():
    msgs = [_obs_msg("app", i) for i in range(1, 5)]
    mw = build_context_pruning_middleware(keep_images=100, keep_marks=2)
    mw.before_model({"messages": msgs}, runtime=None)
    snapshot = [list(m.content) for m in msgs]
    # Second pass must not change already-folded history (stable prefix).
    second = mw.before_model({"messages": msgs}, runtime=None)
    assert second is None
    assert [list(m.content) for m in msgs] == snapshot


def test_context_pruning_image_and_marks_same_message_deduped():
    # Newest keeps both; oldest loses image AND marks in one returned message.
    msgs = [_obs_msg("app", 1), _obs_msg("app", 2), _obs_msg("app", 3)]
    mw = build_context_pruning_middleware(keep_images=2, keep_marks=2)
    result = mw.before_model({"messages": msgs}, runtime=None)

    returned = result["messages"]
    # msgs[0] was hit by both passes but must appear only once.
    assert returned.count(msgs[0]) == 1
    assert _count_images(msgs[0]) == 0
    assert "[marks 已折叠:3]" in _obs_texts(msgs[0])[0]
    # Newest message keeps both image and full marks.
    assert _count_images(msgs[2]) == 1
    assert "marks (3):" in _obs_texts(msgs[2])[0]


def test_context_pruning_first_obs_without_marks_not_folded():
    # Opening HumanMessage's [OBS] app=... has no marks section -> never folded.
    opening = HumanMessage(content=[{"type": "text", "text": "[OBS] app=com.x"}])
    msgs = [opening, _obs_msg("app", 1), _obs_msg("app", 2), _obs_msg("app", 3)]
    mw = build_context_pruning_middleware(keep_images=100, keep_marks=2)
    mw.before_model({"messages": msgs}, runtime=None)
    # The opening block is untouched (no "marks (" marker to fold).
    assert opening.content[0]["text"] == "[OBS] app=com.x"


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


# --------------------------------------------------------------------------
# 3.1 budget-warn middleware (L0 mirror; one-shot at ceil(warn_ratio * limit))
# --------------------------------------------------------------------------
def _budget_text(result) -> str:
    assert result is not None
    return result["messages"][0].content


def test_budget_warn_fires_once_at_threshold():
    mw = build_budget_middleware(max_model_calls=20, warn_ratio=0.8)  # threshold = 16
    # Below threshold: no mirror injected.
    assert mw.before_model({"thread_model_call_count": 15}, runtime=None) is None
    # At threshold: inject exactly one budget-remaining SystemMessage.
    text = _budget_text(mw.before_model({"thread_model_call_count": 16}, runtime=None))
    assert text.startswith("预算余量：已用 16/20")
    assert "剩 4 次" in text
    # One-shot: never fires again even as the count grows.
    assert mw.before_model({"thread_model_call_count": 19}, runtime=None) is None


def test_budget_reset_re_arms_the_one_shot():
    mw = BudgetMiddleware(max_model_calls=10, warn_ratio=0.8)  # threshold = 8
    assert mw.before_model({"thread_model_call_count": 8}, runtime=None) is not None
    assert mw.before_model({"thread_model_call_count": 9}, runtime=None) is None
    mw.reset()
    assert mw.before_model({"thread_model_call_count": 9}, runtime=None) is not None


def test_budget_missing_count_is_tolerated():
    mw = BudgetMiddleware(max_model_calls=20, warn_ratio=0.8)
    # First turn has no merged ModelCallLimit state key yet -> treat as 0.
    assert mw.before_model({}, runtime=None) is None
    assert mw.before_model({"messages": []}, runtime=None) is None


def test_budget_ratio_out_of_range_clamps_to_default():
    # Illegal ratios must neither disable nor over-fire; they fall back to 0.8.
    assert BudgetMiddleware(max_model_calls=20, warn_ratio=0.0).warn_ratio == 0.8
    assert BudgetMiddleware(max_model_calls=20, warn_ratio=5.0).warn_ratio == 0.8
    # A ratio of exactly 1.0 is legal (warn only on the very last call).
    mw = BudgetMiddleware(max_model_calls=10, warn_ratio=1.0)
    assert mw.warn_ratio == 1.0
    assert mw.before_model({"thread_model_call_count": 9}, runtime=None) is None
    assert mw.before_model({"thread_model_call_count": 10}, runtime=None) is not None


def test_budget_english_text():
    mw = BudgetMiddleware(max_model_calls=20, warn_ratio=0.8, lang="en")
    text = _budget_text(mw.before_model({"thread_model_call_count": 16}, runtime=None))
    assert text.startswith("Budget remaining: 16/20 model calls used, 4 left.")
