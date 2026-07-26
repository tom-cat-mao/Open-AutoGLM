import json

from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    build_plan_context_block,
    default_context_budget,
    default_screen_belief,
    normalize_failure_cause,
    sanitize_context_payload,
    select_plan_context,
    update_failure_memory,
    update_summarized_history,
)
from phone_agent.graph.state import messages_reducer


def test_messages_reducer_appends_plan_messages_when_only_role_matches() -> None:
    existing = [{"role": "user", "content": "old"}]
    new = [{"role": "user", "content": "new"}]

    assert messages_reducer(existing, new) == existing + new


def test_messages_reducer_replaces_execute_rebuilt_messages_by_role_and_content() -> (
    None
):
    existing = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
    ]
    rebuilt = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old stripped"},
        {"role": "assistant", "content": "answer"},
    ]

    assert messages_reducer(existing, rebuilt) == rebuilt


def test_messages_reducer_ignores_empty_update() -> None:
    existing = [{"role": "user", "content": "old"}]

    assert messages_reducer(existing, []) == existing


def test_context_defaults_are_json_serializable() -> None:
    payload = {
        "context_mode": "inject",
        "screen_belief": default_screen_belief(),
        "context_budget": default_context_budget(),
        "failure_memory": [],
        "summarized_history": "",
    }

    assert json.loads(json.dumps(payload))["screen_belief"]["summary"] == "unknown"
    assert payload["context_budget"]["context_block_chars"] == 1500


def test_failure_taxonomy_normalization_covers_canonical_labels() -> None:
    for cause in FAILURE_TAXONOMY:
        assert normalize_failure_cause(cause) == cause
    assert normalize_failure_cause("permission_login_captcha") == "permission_or_login_or_captcha"
    assert normalize_failure_cause("bad") == "unknown"


def test_failure_memory_and_history_budget_limits() -> None:
    memory = []
    for index in range(5):
        memory = update_failure_memory(
            memory,
            {"step_count": index, "action": "Tap", "failure_cause": "wrong_page", "current_app": "App"},
        )

    assert len(memory) == 3
    assert memory[0]["step_count"] == 2
    history, truncated = update_summarized_history("x" * 900, {"step_count": 1, "action": "Tap"})
    assert len(history) <= 800
    assert truncated is True


def test_plan_context_block_truncates_and_redacts() -> None:
    block, metrics = build_plan_context_block(
        {
            "screen_belief": {"summary": "张三", "current_app": "App"},
            "action_outcome_summary": {"result_message_summary": "13800138000"},
            "failure_memory": [{"failure_cause": "wrong_page"}],
            "summarized_history": "sk-secret " + "x" * 2000,
            "context_budget": default_context_budget(),
        }
    )

    assert len(block) <= 1500
    assert metrics["context_truncated"] is True
    assert "13800138000" not in block
    assert "sk-secret" not in block


def test_plan_context_block_skips_default_empty_sections() -> None:
    block, metrics = build_plan_context_block({})

    assert block == ""
    assert metrics == {"context_block_chars": 0, "context_truncated": False}


def test_select_plan_context_ignores_default_screen_belief() -> None:
    result = select_plan_context(
        {"screen_belief": default_screen_belief()},
        mode="observe",
        lang="cn",
    )

    assert "screen_belief" not in result.selected_sections


def test_select_plan_context_detects_raw_action_outcome_sources() -> None:
    result = select_plan_context(
        {
            "action_parsed": {"action": "Type"},
            "action_result": {"success": True, "message": "ok"},
        },
        mode="inject",
        lang="cn",
    )

    assert "last_action_outcome" in result.selected_sections
    assert "last_action_outcome" in result.context_block


def test_plan_context_block_marks_task_value_in_derived_fields() -> None:
    block, _metrics = build_plan_context_block(
        {
            "task": "帮我拨 13800138000",
            "action_parsed": {"action": "Type", "text": "13800138000"},
            "action_result": {"success": True, "message": "已输入13800138000"},
            "summarized_history": "step=1 text=13800138000 other=13900139000",
        }
    )

    assert "13800138000" not in block
    assert "13900139000" not in block
    assert "<matches_task_value>" in block
    assert "<redacted>" in block


def test_chinese_ui_text_not_falsely_redacted() -> None:
    """Chinese surname regex was removed; common UI text must survive."""
    from phone_agent.graph.context import redact_context_text

    assert redact_context_text("允许存储权限") != "<redacted>"
    assert redact_context_text("安全设置页面") != "<redacted>"
    assert redact_context_text("黄金会员专属优惠") != "<redacted>"
    assert redact_context_text("马上支付可享优惠") != "<redacted>"
    assert redact_context_text("当前页面显示Wi-Fi设置") != "<redacted>"


def test_inject_context_preserves_model_summary() -> None:
    """Inject mode should keep reflect-generated screen descriptions readable."""
    result = select_plan_context(
        {
            "reflection": "当前页面是设置列表，显示了Wi-Fi、蓝牙、显示等选项",
            "current_app": "com.android.settings",
            "screen_belief": {"summary": "redacted_stub", "current_app": "com.android.settings"},
            "action_parsed": {"action": "Tap", "coordinate": [100, 460]},
            "action_result": {"success": True, "message": "Tapped at (100, 460)"},
            "reflection_verdict": "succeeded",
            "failure_cause": None,
            "suggested_strategy": "continue",
            "failure_memory": [],
            "summarized_history": "step=1 action=Tap success=True",
            "context_budget": default_context_budget(),
        },
        mode="inject",
        lang="cn",
    )

    assert "Wi-Fi" in result.context_block
    assert "蓝牙" in result.context_block
    assert "设置列表" in result.context_block
    assert "Tapped" in result.context_block


def test_inject_context_redacts_phone_in_summary() -> None:
    """Inject mode must still redact regex-matched sensitive data."""
    result = select_plan_context(
        {
            "reflection": "用户手机号是13800138000，请联系",
            "current_app": "com.app",
            "screen_belief": {"summary": "stub", "current_app": "com.app"},
            "reflection_verdict": "failed",
            "failure_cause": "element_not_found",
            "failure_memory": [],
            "summarized_history": "",
            "context_budget": default_context_budget(),
        },
        mode="inject",
        lang="cn",
    )

    assert "13800138000" not in result.context_block
    assert "<redacted>" in result.context_block


def test_sha256_not_double_redacted() -> None:
    """sha256 hash values should not be recursively redacted."""
    payload = {"sha256": "abcdef123456", "summary": "test"}

    result = sanitize_context_payload(payload)

    assert result["sha256"] == "abcdef123456"
    assert not isinstance(result["sha256"], dict)


def test_mark_provider_hints_keep_raw_text_for_local_provider_use() -> None:
    from phone_agent.graph.observation import build_mark_provider_hints

    hints = build_mark_provider_hints(
        task="给 13800138000 发验证码",
        reflection="张三 13900139000 请重试",
        action={"action": "Type", "text": "VillageThomas"},
        provider_hints=[{"text": "订单123456 搜索按钮", "source": "secret-13800138000", "action": "tap-13900139000"}],
        max_hints=4,
    )

    raw = json.dumps([hint.__dict__ for hint in hints], ensure_ascii=False)
    assert "13800138000" in raw
    assert "13900139000" in raw
    assert "订单123456" in raw
    assert "VillageThomas" in raw
    assert "secret-13800138000" not in raw
    assert "tap-13900139000" not in raw


def test_observation_rejects_provider_marks_with_hash_mismatch() -> None:
    from phone_agent.graph.observation import build_observation
    from phone_agent.grounding.fake import FakeGroundingProvider

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400], provider_input_hash="input-hash")
    original = provider.provide_marks

    def mismatched(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.__class__(**{**result.to_dict(), "raw_screenshot_hash": "other"})

    provider.provide_marks = mismatched  # type: ignore[method-assign]
    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=[],
    )

    assert observation.mark_registry.marks == {}
    provider_summary = observation.mark_provider_observation["providers"][0]
    assert provider_summary["failure_code"] == "hash_mismatch"
    assert provider_summary["marks"] == []


def test_unknown_provider_receives_redacted_hints_by_default() -> None:
    from typing import Any

    from phone_agent.graph.observation import build_mark_provider_hints, build_observation
    from phone_agent.grounding.provider import MarkProviderResult

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    class RemoteLikeProvider:
        name = "remote_like"
        version = "test"

        def __init__(self) -> None:
            self.seen_hints: list[str] | None = None

        def provide_marks(self, screenshot: Any, screen_binding: Any, hints=None, timeout=None):
            self.seen_hints = [hint.description() for hint in hints or []]
            return MarkProviderResult(
                success=False,
                provider=self.name,
                failure_code="grounding_no_candidate",
                message="no marks",
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="input-hash",
            )

    provider = RemoteLikeProvider()
    hints = build_mark_provider_hints(task="点击屏幕上的 13800138000 联系人")
    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=hints,
    )

    assert provider.seen_hints == ["点击屏幕上的 <redacted> 联系人"]
    assert "13800138000" not in json.dumps(observation.mark_provider_observation, ensure_ascii=False)


def test_provider_with_raw_hint_opt_out_receives_redacted_hints() -> None:
    from typing import Any

    from phone_agent.graph.observation import build_mark_provider_hints, build_observation
    from phone_agent.grounding.provider import MarkProviderResult

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    class OptOutProvider:
        name = "opt_out"
        version = "test"
        allow_raw_hints = False

        def __init__(self) -> None:
            self.seen_hints: list[str] | None = None

        def provide_marks(self, screenshot: Any, screen_binding: Any, hints=None, timeout=None):
            self.seen_hints = [hint.description() for hint in hints or []]
            return MarkProviderResult(
                success=False,
                provider=self.name,
                failure_code="grounding_no_candidate",
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="input-hash",
            )

    provider = OptOutProvider()
    hints = build_mark_provider_hints(task="点击屏幕上的 13800138000 联系人")
    build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=hints,
    )

    assert provider.seen_hints == ["点击屏幕上的 <redacted> 联系人"]


def test_observation_rejects_failed_provider_marks_even_when_present() -> None:
    from phone_agent.graph.observation import build_observation
    from phone_agent.grounding.fake import FakeGroundingProvider

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    original = provider.provide_marks

    def failed_with_marks(*args, **kwargs):
        result = original(*args, **kwargs)
        bad_mark = {
            "mark_id": "secret-13800138000-" + "x" * 120,
            "bbox": ["bad"],
            "center": ["bad"],
            "confidence": 1.0,
            "source": "source-13900139000-" + "x" * 120,
            "valid": True,
            "reason": "reason-订单123456-" + "x" * 120,
            "role": "button",
            "text_summary": "private 13800138000",
        }
        return result.__class__(
            **{
                **result.to_dict(),
                "success": False,
                "failure_code": "low_confidence",
                "message": "private 13800138000",
                "marks": [bad_mark],
            }
        )

    provider.provide_marks = failed_with_marks  # type: ignore[method-assign]
    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=[],
    )

    assert observation.mark_registry.marks == {}
    provider_summary = observation.mark_provider_observation["providers"][0]
    assert provider_summary["failure_code"] == "low_confidence"
    assert provider_summary["marks"] == []
    assert "13800138000" not in json.dumps(provider_summary, ensure_ascii=False)
    assert "13900139000" not in json.dumps(provider_summary, ensure_ascii=False)
    assert "订单123456" not in json.dumps(provider_summary, ensure_ascii=False)


def test_observation_bounds_successful_provider_metadata() -> None:
    from phone_agent.graph.observation import build_observation
    from phone_agent.grounding.fake import FakeGroundingProvider

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    original = provider.provide_marks

    def malicious_metadata(*args, **kwargs):
        result = original(*args, **kwargs)
        bad_hints = [
            {
                "source": "source-13800138000-" + "x" * 1000,
                "action": "tap-13900139000-" + "x" * 1000,
                "text_length": 999999999999,
            }
            for _ in range(20)
        ]
        return result.__class__(
            **{
                **result.to_dict(),
                "provider": "provider-13800138000-" + "x" * 1000,
                "provider_input_hash": "hash-13900139000-" + "x" * 1000,
                "status": "status-订单123456-" + "x" * 1000,
                "hints": bad_hints,
            }
        )

    provider.provide_marks = malicious_metadata  # type: ignore[method-assign]
    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=[],
    )

    provider_summary = observation.mark_provider_observation["providers"][0]
    raw = json.dumps(provider_summary, ensure_ascii=False)
    assert "13800138000" not in raw
    assert "13900139000" not in raw
    assert "订单123456" not in raw
    assert len(provider_summary["provider"]) <= 64
    assert len(provider_summary["provider_input_hash"]) <= 64
    assert len(provider_summary["status"]) <= 64
    assert len(provider_summary["hints"]) == 5


def test_observation_sanitizes_successful_provider_mark_summary_fields() -> None:
    from phone_agent.graph.observation import build_observation
    from phone_agent.grounding.fake import FakeGroundingProvider

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    provider = FakeGroundingProvider(bbox=[100, 200, 300, 400])
    original = provider.provide_marks

    def malicious_mark_fields(*args, **kwargs):
        result = original(*args, **kwargs)
        bad_mark = {
            "mark_id": "id-13800138000-" + "x" * 1000,
            "bbox": ["secret-13900139000", {}, [], 5000],
            "center": ["bad", 2],
            "confidence": "secret",
            "source": "source-订单123456-" + "x" * 1000,
            "valid": "yes",
            "reason": "reason-13800138000-" + "x" * 1000,
            "role": object(),
            "text_summary": object(),
        }
        return result.__class__(**{**result.to_dict(), "marks": [bad_mark]})

    provider.provide_marks = malicious_mark_fields  # type: ignore[method-assign]
    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[provider],
        provider_hints=[],
    )

    mark_summary = observation.mark_provider_observation["providers"][0]["marks"][0]
    raw = json.dumps(mark_summary, ensure_ascii=False)
    assert "13800138000" not in raw
    assert "13900139000" not in raw
    assert "订单123456" not in raw
    assert mark_summary["bbox"] == []
    assert mark_summary["center"] == []
    assert mark_summary["confidence"] is None
    assert mark_summary["valid"] is False
    assert len(mark_summary["mark_id"]) <= 64
    assert len(mark_summary["source"]) <= 64
    assert len(mark_summary["reason"]) <= 64


def test_observation_includes_bounded_fallback_chain_metadata() -> None:
    from phone_agent.graph.observation import build_observation
    from phone_agent.grounding.provider import MarkCandidate, MarkProviderResult

    class Screenshot:
        base64_data = "fake-image"
        width = 1000
        height = 2000

    class Provider:
        name = "fallback"
        version = "test"
        allow_raw_hints = False

        def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
            mark = MarkCandidate(mark_id="m1", bbox=[100, 200, 300, 400], center=[200, 300])
            return MarkProviderResult(
                success=True,
                provider="fallback",
                screen_id=screen_binding.screen_id,
                raw_screenshot_hash=screen_binding.raw_screenshot_hash,
                provider_input_hash="hash",
                marks=[mark],
                candidates=[mark],
                candidate_count=1,
                metadata={
                    "fallback_chain": [
                        {
                            "provider": "accessibility_tree",
                            "success": True,
                            "failure_code": "secret-13800138000",
                            "candidate_count": 3,
                            "mark_count": 3,
                            "structure_count": 2,
                            "latency_ms": 4,
                            "usable": False,
                            "skip_reason": "accessibility_dump_callback_missing",
                            "raw_xml": "<secret>",
                        }
                    ],
                    "hybrid_factory": {
                        "hybrid_mode": True,
                        "accessibility_child_enabled": False,
                        "accessibility_child_skip_reason": "accessibility_dump_callback_missing",
                        "provider_order": ["accessibility_tree", "locateanything_mlx", "secret-13800138000"],
                        "raw_hint": "点击 13900139000",
                    },
                    "parse_summary": {
                        "xml_status": "ok",
                        "raw_node_count": 4,
                        "mark_count": 3,
                        "structure_node_count": 4,
                        "bounds_parse_fail_count": 1,
                        "filtered_zero_area_count": 1,
                        "interactive_candidate_count": 3,
                        "raw_xml": "<secret>",
                    },
                },
            )

    observation = build_observation(
        screenshot=Screenshot(),
        current_app="Settings",
        mark_providers=[Provider()],
        provider_hints=[],
    )

    metadata = observation.mark_provider_observation["providers"][0]["metadata"]
    assert metadata["fallback_chain"][0]["provider"] == "accessibility_tree"
    assert metadata["fallback_chain"][0]["usable"] is False
    assert metadata["fallback_chain"][0]["structure_count"] == 2
    assert metadata["fallback_chain"][0]["skip_reason"] == "accessibility_dump_callback_missing"
    assert metadata["hybrid_factory"]["accessibility_child_skip_reason"] == "accessibility_dump_callback_missing"
    assert metadata["hybrid_factory"]["provider_order"][:2] == ["accessibility_tree", "locateanything_mlx"]
    assert metadata["parse_summary"]["bounds_parse_fail_count"] == 1
    raw = json.dumps(metadata, ensure_ascii=False)
    assert "13800138000" not in raw
    assert "13900139000" not in raw
    assert "raw_xml" not in raw
    assert "raw_hint" not in raw
