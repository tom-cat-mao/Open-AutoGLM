import json

from phone_agent.graph.context import (
    FAILURE_TAXONOMY,
    build_plan_context_block,
    default_context_budget,
    default_screen_belief,
    normalize_failure_cause,
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
        "context_mode": "observe",
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
    assert "张三" not in block
    assert "13800138000" not in block
    assert "sk-secret" not in block
