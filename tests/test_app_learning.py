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


def test_launch_tool_does_not_record_learning_but_carries_metadata(
    fake_device,
) -> None:
    """F7: an `am start` command-level success never learns — the mapping is
    only recorded by the reflect step after foreground verification. The
    resolved (term, package) rides out as add-only result metadata instead."""
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
    assert learning.lookup("易车") is None
    assert learning.snapshot() == {}
    assert result.metadata == {
        "launch_app_term": "易车",
        "launch_resolved_package": "com.yiche.app",
    }
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


def test_launch_fetches_inventory_once_and_passes_it_to_launch_app(
    fake_device,
) -> None:
    """F8: one launch = one `pm list packages`. The tool fetches the inventory
    once and hands it to device.launch_app, which must not re-fetch it."""
    captured: dict = {}
    original = fake_device.launch_app

    def spy(app, device_id=None, **kwargs):
        captured["inventory_passed"] = kwargs.get("inventory") is not None
        return original(app, device_id, **kwargs)

    fake_device.launch_app = spy  # type: ignore[method-assign]
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
    inventory_calls = [
        call[0] for call in fake_device.calls if call[0] == "get_installed_app_inventory"
    ]
    assert len(inventory_calls) == 1
    assert captured["inventory_passed"] is True


# ----------------------------------------------------------------------
# F7: learning is recorded by reflect only after foreground verification
# ----------------------------------------------------------------------


def _reflect_config_with_learning(model, device, learning) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": device,
            "verbose": False,
            "grounding_provider_name": "off",
            "app_learning_context": learning,
        }
    }


def _launch_action_result(term: str = "FakeApp", package: str = "com.fake.app") -> dict:
    return {
        "success": True,
        "should_finish": False,
        "metadata": {
            "launch_app_term": term,
            "launch_resolved_package": package,
        },
    }


def test_reflect_does_not_record_when_foreground_mismatches(base_state, fake_device) -> None:
    """F7: am start succeeded but the foreground app differs from the launch
    target → the verifier reports launch_matched=False and nothing is learned
    (the wrong-mapping self-certification is impossible this run)."""
    from phone_agent.graph.nodes.reflect import reflect_node
    from tests.graph.test_p5_reflect_skip import (
        CountingModelClient,
        _launch_state,
        _programmatic_contract,
    )

    fake_device.get_current_app = lambda device_id=None: "OtherApp"  # type: ignore[method-assign]
    learning = RuntimeAppLearningContext()
    state = _launch_state(base_state, _programmatic_contract())
    state["action_result"] = _launch_action_result()

    reflect_node(
        state, _reflect_config_with_learning(CountingModelClient(), fake_device, learning)
    )

    assert learning.snapshot() == {}


def test_reflect_records_learning_after_foreground_match(base_state, fake_device) -> None:
    """F7: the verifier confirms the foreground matches the launch target →
    the resolved (term, package) from action_result metadata is recorded."""
    from phone_agent.graph.nodes.reflect import reflect_node
    from tests.graph.test_p5_reflect_skip import (
        CountingModelClient,
        _launch_state,
        _programmatic_contract,
    )

    learning = RuntimeAppLearningContext()
    state = _launch_state(base_state, _programmatic_contract())
    state["action_result"] = _launch_action_result()

    result = reflect_node(
        state, _reflect_config_with_learning(CountingModelClient(), fake_device, learning)
    )

    assert result["verifier_status"] == "success"
    assert learning.lookup("FakeApp") == "com.fake.app"


def test_learned_mapping_backs_resolver_after_foreground_verify() -> None:
    """F7: once a mapping is learned (post-verification), the resolver takes
    the learned path on later launches — the fast path the verifier also
    reads first."""
    from phone_agent.config.apps import (
        DEFAULT_APP_REGISTRY,
        DEFAULT_LAUNCH_POLICY,
    )
    from phone_agent.config.app_registry import LaunchTargetResolver

    learning = RuntimeAppLearningContext()
    learning.record("同程", "com.tongcheng.android")
    inventory = InstalledAppInventory(
        frozenset({"com.tongcheng.android"}), device_id="serial"
    )

    target = LaunchTargetResolver(
        DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY
    ).resolve("同程", inventory=inventory, learning=learning)

    assert target.status == "resolved"
    assert target.package_name == "com.tongcheng.android"
