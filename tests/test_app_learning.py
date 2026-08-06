"""Deterministic tests for the per-run app learning cache and its consumers.

Covers: RuntimeAppLearningContext semantics; launch-tool learning write and
reuse; verifier cache-first package resolution; compiler summary including
learned mappings and installed-device top-N; launch-decision trace emission.
All device boundaries use synthetic fakes — never model-behavior mocks.
"""

from __future__ import annotations

import json

import pytest

from phone_agent.config.app_registry import InstalledAppInventory
from phone_agent.config.apps import get_app_registry_summary
from phone_agent.graph.runtime_app_learning import RuntimeAppLearningContext
from phone_agent.graph.tools import dispatch_tool
from phone_agent.graph.tools.runtime import (
    reset_tool_app_learning,
    set_tool_app_learning,
)


def _dispatch_with_learning(action, fake_device, learning, *, device_id="device-1"):
    token = set_tool_app_learning(learning)
    try:
        return dispatch_tool(action, 1000, 2000, device_id, device_factory=fake_device)
    finally:
        reset_tool_app_learning(token)


def test_learning_context_records_and_looks_up_normalized_terms() -> None:
    learning = RuntimeAppLearningContext()

    learning.record("  同程 ", "com.tongcheng.android")
    learning.record("Tongcheng", "com.tongcheng.android")

    assert learning.lookup("同程") == "com.tongcheng.android"
    assert learning.lookup("  TONGCHENG  ") == "com.tongcheng.android"
    assert learning.lookup("missing") is None


def test_learning_context_ignores_empty_terms() -> None:
    learning = RuntimeAppLearningContext()

    learning.record("", "com.example.pkg")
    learning.record("某应用", "  ")

    assert learning.snapshot() == {}


def test_learning_context_snapshot_is_sorted() -> None:
    learning = RuntimeAppLearningContext()

    learning.record("beta", "com.example.beta")
    learning.record("alpha", "com.example.alpha")

    assert list(learning.snapshot().items()) == [
        ("alpha", "com.example.alpha"),
        ("beta", "com.example.beta"),
    ]


def test_learning_context_is_not_serializable() -> None:
    learning = RuntimeAppLearningContext()

    with pytest.raises(TypeError, match="not serializable"):
        learning.__getstate__()


def test_launch_tool_records_learning_after_candidate_success(fake_device) -> None:
    learning = RuntimeAppLearningContext()
    result = _dispatch_with_learning(
        {
            "_metadata": "do",
            "action": "Launch",
            "app": "易车",
            "package_candidates": ["com.yiche.app"],
        },
        fake_device,
        learning,
    )

    assert result.success is True
    assert learning.lookup("易车") == "com.yiche.app"
    assert [call[0] for call in fake_device.calls] == [
        "get_installed_app_inventory",
        "launch_app",
    ]


def test_launch_tool_reuses_learning_without_candidates(fake_device) -> None:
    learning = RuntimeAppLearningContext()
    learning.record("易车", "com.yiche.app")

    result = _dispatch_with_learning(
        {"_metadata": "do", "action": "Launch", "app": "易车"},
        fake_device,
        learning,
    )

    assert result.success is True
    assert [call[0] for call in fake_device.calls] == [
        "get_installed_app_inventory",
        "launch_app",
    ]


def test_launch_tool_unknown_without_candidates_is_informative(fake_device) -> None:
    result = _dispatch_with_learning(
        {"_metadata": "do", "action": "Launch", "app": "某个未安装应用"},
        fake_device,
        RuntimeAppLearningContext(),
    )

    assert result.success is False
    assert "未在设备找到该应用，可从桌面图标启动" in result.message


def test_verifier_prefers_learning_cache_over_static_table() -> None:
    from phone_agent.graph.verifier import _package_for_app_name

    learning = RuntimeAppLearningContext()
    # Override a static mapping: the learned mapping must win.
    learning.record("微信", "com.example.other")

    assert _package_for_app_name("微信", learning=learning) == "com.example.other"
    assert _package_for_app_name("微信") == "com.tencent.mm"


def test_verifier_launch_match_via_learning(base_state) -> None:
    from phone_agent.graph.verifier import verify_action_outcome

    learning = RuntimeAppLearningContext()
    learning.record("易车", "com.yiche.app")
    state = {
        **base_state,
        "action_parsed": {
            "_metadata": "do",
            "action": "Launch",
            "app": "易车",
        },
    }

    result = verify_action_outcome(
        before_state=state,
        after_screenshot=None,
        after_app="com.yiche.app",
        action_result={"success": True, "should_finish": False},
        learning=learning,
    )

    assert result.status == "success"
    assert result.signals.get("launch_matched") is True


def test_registry_summary_includes_learned_mapping() -> None:
    learning = RuntimeAppLearningContext()
    learning.record("易车", "com.yiche.app")

    summary = get_app_registry_summary(lang="cn", learning=learning)

    assert "com.yiche.app" in summary
    assert "易车" in summary


def test_registry_summary_includes_installed_device_top_n() -> None:
    inventory = InstalledAppInventory(
        frozenset(
            {
                "com.zzz.last",
                "com.aaa.first",
                "com.mmm.middle",
            }
        ),
        device_id="serial",
    )

    summary = get_app_registry_summary(
        lang="cn", inventory=inventory, installed_top_n=2
    )

    assert "com.aaa.first" in summary
    assert "com.mmm.middle" in summary
    assert "com.zzz.last" not in summary
    assert "设备实装应用" in summary


def test_registry_summary_without_runtime_data_is_unchanged() -> None:
    plain = get_app_registry_summary(lang="cn")
    explicit = get_app_registry_summary(lang="cn", learning=None, inventory=None)

    assert plain == explicit
    assert "可用应用" in plain


def test_execute_node_emits_launch_decision_trace(
    base_state, fake_device, tmp_path
) -> None:
    from phone_agent.graph.nodes.execute import execute_node
    from phone_agent.graph.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(
        trace_id="launch-decision", trace_dir=tmp_path, redact=False
    )
    state = {
        **base_state,
        "action_parsed": {
            "_metadata": "do",
            "action": "Launch",
            "app": "易车",
            "package_candidates": ["com.yiche.app"],
        },
    }

    execute_node(
        state,
        {
            "configurable": {
                "device_factory": fake_device,
                "trace_writer": writer,
                "verbose": False,
            }
        },
    )

    records = [
        json.loads(line)
        for line in writer.path.read_text(encoding="utf-8").splitlines()
    ]
    decisions = [record for record in records if record["event"] == "launch_decision"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["status"] == "resolved"
    assert decisions[0]["payload"]["package_name"] == "com.yiche.app"
