"""Goal compiler tests for the shared AppRegistry vocabulary."""

import pytest

from phone_agent.graph.goal_compiler import (
    GOAL_COMPILER_SYSTEM_PROMPT_CN,
    GOAL_COMPILER_SYSTEM_PROMPT_EN,
    HeuristicGoalCompiler,
    LLMGoalCompiler,
)


@pytest.mark.parametrize(
    ("task", "canonical_id"),
    [
        ("Open Chrome", "chrome"),
        ("打开小红书", "小红书"),
        ("去高德地图查看路线", "高德地图"),
        ("去b站看视频", "bilibili"),
    ],
)
def test_heuristic_compiler_uses_shared_registry(task: str, canonical_id: str) -> None:
    contract = HeuristicGoalCompiler().compile(task=task)

    assert contract.target_app_hint == canonical_id


def test_goal_compiler_prompts_do_not_define_a_five_app_closed_world() -> None:
    forbidden = "bilibili|wechat|douyin|xiaohongshu|settings"

    assert forbidden not in GOAL_COMPILER_SYSTEM_PROMPT_CN
    assert forbidden not in GOAL_COMPILER_SYSTEM_PROMPT_EN


def test_llm_compiler_normalizes_declared_app_through_registry() -> None:
    compiler = LLMGoalCompiler(model_client=object())

    contract = compiler._parse_compiled_contract(
        {
            "objective": "Open Chrome",
            "success_criteria": [
                {
                    "name": "app_open",
                    "description": "Chrome is foreground",
                    "verification": "app_or_activity_match",
                    "required": True,
                }
            ],
            "target_app_hint": "Google Chrome",
        },
        task="Open Chrome",
    )

    assert contract.target_app_hint == "chrome"


def test_llm_compiler_rejects_unknown_app_hint_instead_of_guessing() -> None:
    compiler = LLMGoalCompiler(model_client=object())

    with pytest.raises(ValueError, match="unresolvable target_app_hint: unknown"):
        compiler._parse_compiled_contract(
            {
                "objective": "Open an unknown app",
                "success_criteria": [
                    {
                        "name": "app_open",
                        "description": "app is foreground",
                        "verification": "app_or_activity_match",
                        "required": True,
                    }
                ],
                "target_app_hint": "definitely-not-installed-or-known",
            },
            task="Open an unknown app",
        )
