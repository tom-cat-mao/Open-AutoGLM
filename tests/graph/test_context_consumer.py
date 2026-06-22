"""Tests for the consumer-aware sanitization model introduced in this refactor.

The new model:
* state-write paths apply regex-only redaction (no stub).
* stub policy fires only when ``sanitize_context_payload`` is invoked with
  ``consumer="checkpoint"`` — used by :class:`RedactingSerializer`.
* the legacy ``inject=True|False`` bool is retained as a backward-compatible
  alias that maps onto the new consumer tags.
"""

from __future__ import annotations

import json

from phone_agent.graph.context import (
    CONSUMER_POLICY,
    build_action_outcome_summary,
    build_screen_belief,
    sanitize_context_payload,
    sanitize_context_text_regex,
)


def test_consumer_policy_covers_documented_consumers() -> None:
    for consumer in ("inject", "reflect_prompt", "trace_payload", "checkpoint", "default"):
        assert consumer in CONSUMER_POLICY


def test_sanitize_context_payload_inject_true_is_regex_only() -> None:
    """inject=True (legacy) == consumer='inject' == regex-only."""
    payload = {"message": "短信已发送至13800138000", "current_app": "Chat"}
    assert sanitize_context_payload(payload, inject=True) == sanitize_context_payload(
        payload, consumer="inject"
    )
    result = sanitize_context_payload(payload, consumer="inject")
    assert "13800138000" not in result["message"]
    assert result["current_app"] == "Chat"
    assert isinstance(result["message"], str)


def test_sanitize_context_payload_inject_false_is_checkpoint_stub() -> None:
    """inject=False (legacy) == consumer='checkpoint' == key-level stub."""
    payload = {"message": "短信已发送至13800138000"}
    assert sanitize_context_payload(payload, inject=False) == sanitize_context_payload(
        payload, consumer="checkpoint"
    )
    result = sanitize_context_payload(payload, consumer="checkpoint")
    assert isinstance(result["message"], dict)
    assert result["message"]["redacted"] is True
    assert result["message"]["length"] == len("短信已发送至13800138000")


def test_sanitize_context_payload_default_consumer_is_regex_only() -> None:
    """No inject, no consumer -> 'default' policy -> regex-only."""
    payload = {"message": "短信已发送至13800138000"}
    result = sanitize_context_payload(payload)
    assert isinstance(result["message"], str)
    assert "13800138000" not in result["message"]
    assert "<redacted>" in result["message"]


def test_sanitize_context_payload_marks_task_matches_for_reflect_and_inject_only() -> None:
    payload = {
        "message": "已输入13800138000，另一个号码13900139000",
        "status": "ok",
    }

    reflected = sanitize_context_payload(
        payload,
        consumer="reflect_prompt",
        task_context="帮我拨 13800138000",
    )
    injected = sanitize_context_payload(
        payload,
        consumer="inject",
        task_context="帮我拨 13800138000",
    )
    traced = sanitize_context_payload(
        payload,
        consumer="trace_payload",
        task_context="帮我拨 13800138000",
    )

    assert reflected["message"] == "已输入<matches_task_value>，另一个号码<redacted>"
    assert injected["message"] == reflected["message"]
    assert "<matches_task_value>" not in traced["message"]
    assert "13800138000" not in traced["message"]
    assert "13900139000" not in traced["message"]


def test_sanitize_context_payload_checkpoint_ignores_task_context() -> None:
    result = sanitize_context_payload(
        {"message": "已输入13800138000"},
        consumer="checkpoint",
        task_context="帮我拨 13800138000",
    )

    assert isinstance(result["message"], dict)
    assert result["message"]["redacted"] is True


def test_sanitize_context_text_regex_is_idempotent() -> None:
    """Applying the regex helper twice must equal applying it once."""
    text = "短信已发送至13800138000，邮箱 foo@example.com"
    once = sanitize_context_text_regex(text)
    twice = sanitize_context_text_regex(once)
    assert once == twice
    assert "13800138000" not in once
    assert "foo@example.com" not in once
    assert "短信已发送至" in once


def test_build_action_outcome_summary_does_not_stub_message() -> None:
    """State-write path: message is regex-redacted, never stubbed."""
    state = {
        "action_parsed": {"action": "Tap"},
        "action_result": {"success": True, "message": "短信已发送至13800138000"},
        "step_count": 3,
        "current_app": "Chat",
        "reflection_verdict": "succeeded",
    }
    outcome = build_action_outcome_summary(state)
    assert isinstance(outcome["result_message_summary"], str)
    assert "13800138000" not in outcome["result_message_summary"]
    assert "<redacted>" in outcome["result_message_summary"]
    assert outcome["action"] == "Tap"
    assert outcome["current_app"] == "Chat"
    # Must be JSON-serializable (no stub dicts under string-typed keys).
    json.dumps(outcome, ensure_ascii=False)


def test_build_screen_belief_summary_is_regex_redacted_not_stubbed() -> None:
    belief = build_screen_belief(
        current_app="Chat",
        step_count=2,
        summary="请联系13800138000获取信息",
    )
    assert isinstance(belief["summary"], str)
    assert "13800138000" not in belief["summary"]
    assert "请联系" in belief["summary"]


def test_build_action_outcome_summary_handles_missing_message() -> None:
    state = {"action_parsed": {"action": "Wait"}, "action_result": {"success": False}}
    outcome = build_action_outcome_summary(state)
    assert outcome["result_message_summary"] is None


def test_redacting_serializer_stubs_private_keys_on_dumps() -> None:
    """End-to-end: RedactingSerializer wraps a stub inner and applies
    checkpoint policy before delegating to the inner serializer.
    """
    from phone_agent.checkpoint.serde import RedactingSerializer

    class _FakeInner:
        """Records what was handed to dumps; round-trips via JSON."""

        def __init__(self) -> None:
            self.last_dumped = None

        def dumps(self, value):
            self.last_dumped = value
            return json.dumps(value).encode("utf-8")

        def loads(self, data):
            return json.loads(data.decode("utf-8"))

    inner = _FakeInner()
    serde = RedactingSerializer(inner=inner)

    payload = {
        "channel_values": {
            "screen_belief": {"summary": "短信已发送至13800138000", "current_app": "Chat"},
            "action_outcome_summary": {
                "result_message_summary": "短信已发送至13900139000",
                "action": "Tap",
            },
            "reflection": "用户手机号13811112222显示在屏幕上",
        }
    }
    data = serde.dumps(payload)
    # Inner received the checkpoint-policy form: private keys stubbed,
    # non-private strings regex-redacted.
    dumped = inner.last_dumped
    # screen_belief.summary is NOT in PRIVATE_CONTEXT_TEXT_KEYS → regex only.
    assert isinstance(dumped["channel_values"]["screen_belief"]["summary"], str)
    assert "13800138000" not in dumped["channel_values"]["screen_belief"]["summary"]
    assert "<redacted>" in dumped["channel_values"]["screen_belief"]["summary"]
    # current_app is not a private key; regex-redacted (no sensitive pattern
    # here, so preserved as-is).
    assert dumped["channel_values"]["screen_belief"]["current_app"] == "Chat"
    # action key is preserved; result_message_summary IS a private key → stub.
    assert dumped["channel_values"]["action_outcome_summary"]["action"] == "Tap"
    assert isinstance(
        dumped["channel_values"]["action_outcome_summary"]["result_message_summary"], dict
    )
    assert dumped["channel_values"]["action_outcome_summary"]["result_message_summary"]["redacted"] is True
    # reflection (top-level private key) is stubbed.
    assert isinstance(dumped["channel_values"]["reflection"], dict)
    assert dumped["channel_values"]["reflection"]["redacted"] is True
    # Round-trip loads returns the stubbed form.
    assert serde.loads(data) == dumped


def test_redacting_serializer_collapses_full_sidecars_on_dumps() -> None:
    from phone_agent.checkpoint.serde import RedactingSerializer

    class _FakeInner:
        def __init__(self) -> None:
            self.last_dumped = None

        def dumps(self, value):
            self.last_dumped = value
            return json.dumps(value).encode("utf-8")

        def loads(self, data):
            return json.loads(data.decode("utf-8"))

    inner = _FakeInner()
    serde = RedactingSerializer(inner=inner)
    payload = {
        "channel_values": {
            "screen_structure": {
                "screen_id": "screen-1",
                "topology_digest": "topo",
                "nodes": {
                    "node_1": {
                        "text_summary": "张三的私密标题",
                        "content_desc_summary": "地址 北京市海淀区",
                    }
                },
            },
            "object_registry": {
                "screen_id": "screen-1",
                "object_set_version": "objv",
                "objects": {
                    "obj_1": {
                        "object_type": "video",
                        "evidence_summary": "张三的私密标题",
                        "sensitivity_evidence_summary": "验证码 123456",
                    }
                },
            },
        }
    }

    serde.dumps(payload)
    dumped = inner.last_dumped["channel_values"]
    raw = json.dumps(dumped, ensure_ascii=False)
    assert "张三" not in raw
    assert "北京市" not in raw
    assert "123456" not in raw
    assert "nodes" not in dumped["screen_structure"]
    assert "objects" not in dumped["object_registry"]
    assert dumped["screen_structure"]["node_count"] == 1
    assert dumped["object_registry"]["object_type_counts"] == {"video": 1}


def test_redacting_serializer_loads_is_passthrough() -> None:
    from phone_agent.checkpoint.serde import RedactingSerializer

    class _FakeInner:
        def dumps(self, value):
            return json.dumps(value).encode("utf-8")

        def loads(self, data):
            return json.loads(data.decode("utf-8"))

    serde = RedactingSerializer(inner=_FakeInner())
    assert serde.loads(b'{"a": 1}') == {"a": 1}
