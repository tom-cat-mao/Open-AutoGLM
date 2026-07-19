import time

import pytest

from phone_agent.config.policy import DEFAULT_SAFETY_POLICY
from phone_agent.graph.compatibility_adapters import (
    COMPATIBILITY_TELEMETRY,
    DEFAULT_LEGACY_PAGE_SIGNAL_ADAPTER,
)
from phone_agent.graph.goal_requirements import TaskRequirementExtractor
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    EvidenceReference,
    Matcher,
    ObservedFact,
)
from phone_agent.graph.verifier import verify_action_outcome


@pytest.mark.parametrize(
    ("task", "operation"),
    [
        ("打开设置", "launch"),
        ("在浏览器搜索任意主题", "search"),
        ("选择第2个项目", "select"),
        ("输入任意文本", "input"),
        ("开启一个开关", "toggle"),
        ("执行未指定的神秘操作", "unknown"),
    ],
)
def test_requirement_extraction_covers_six_task_families(
    task: str, operation: str
) -> None:
    assert TaskRequirementExtractor().extract(task).operation_kind == operation


@pytest.mark.parametrize(
    ("expected", "observed", "status"),
    [
        ("Silverstone", "Silverstone", "matched"),
        ("Singapore", "Singapore", "matched"),
        ("Arbitrary-Renamed-Entity-A", "Arbitrary-Renamed-Entity-A", "matched"),
        ("Arbitrary-Renamed-Entity-A", "Arbitrary-Renamed-Entity-B", "contradicted"),
        ("主题甲", "主题甲", "matched"),
        ("主题甲", "主题乙", "contradicted"),
    ],
)
def test_semantic_matching_is_metamorphic_not_domain_named(
    expected: str, observed: str, status: str
) -> None:
    spec = CORE_PREDICATE_CATALOG.create_spec("semantic.entity_matches", expected)
    fact = ObservedFact(
        predicate_id="semantic.entity_matches",
        observed_value=observed,
        confidence=0.95,
        source="visual_region",
        evidence_reference=EvidenceReference(
            source_kind="visual_region",
            reference_id="region-1",
            screen_id="screen-1",
            observation_epoch=1,
            bbox=(0, 0, 1000, 1000),
        ),
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=1,
        provider_version="metamorphic-v1",
    )

    assert Matcher.match(spec, fact).status == status


class _Screenshot:
    base64_data = "current"
    width = 100
    height = 100


def test_legacy_adapter_rollout_switch_and_telemetry_preserve_migration_evidence() -> (
    None
):
    before = COMPATIBILITY_TELEMETRY.snapshot()
    state = {
        "action_parsed": {"_metadata": "do", "action": "Tap"},
        "expected_outcome": {
            "kind": "generic",
            "object_type": "video",
            "expected_page_type": "detail_or_player",
            "expected_rank": 1,
        },
    }
    observation = {"visible_text": "播放器 暂停"}

    enabled = verify_action_outcome(
        before_state=state,
        after_screenshot=_Screenshot(),
        after_app="Example",
        action_result={"success": True},
        after_observation=observation,
        page_signal_adapter=DEFAULT_LEGACY_PAGE_SIGNAL_ADAPTER,
    )
    after_enabled = COMPATIBILITY_TELEMETRY.snapshot()
    disabled = verify_action_outcome(
        before_state=state,
        after_screenshot=_Screenshot(),
        after_app="Example",
        action_result={"success": True},
        after_observation=observation,
        page_signal_adapter=None,
    )
    after_disabled = COMPATIBILITY_TELEMETRY.snapshot()

    assert enabled.status == disabled.status
    assert enabled.status != "success"
    assert after_enabled.get("legacy_page_detail_signal", 0) > before.get(
        "legacy_page_detail_signal", 0
    )
    assert after_disabled == after_enabled


def test_policy_and_matcher_regression_budget_is_bounded() -> None:
    spec = CORE_PREDICATE_CATALOG.create_spec("app.foreground_identity", "example")
    fact = ObservedFact(
        predicate_id="app.foreground_identity",
        observed_value="example",
        confidence=1.0,
        source="device",
        evidence_reference=EvidenceReference(
            source_kind="device",
            reference_id="snapshot",
            screen_id="screen-1",
            observation_epoch=1,
        ),
        contract_id="contract-1",
        screen_id="screen-1",
        observation_epoch=1,
        provider_version="device-v1",
    )

    started = time.perf_counter()
    for _ in range(1000):
        DEFAULT_SAFETY_POLICY.classify(text="open ordinary settings page")
        Matcher.match(spec, fact)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
