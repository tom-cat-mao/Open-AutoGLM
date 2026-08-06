"""P2 milestone latch: plan-side goal_agenda lock-in across transient staleness.

The latch is display-only. Acceptance keeps strict ``current_observation``
freshness semantics and never reads ``goal_agenda`` / ``latched`` fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from phone_agent.graph.context import (
    CONTEXT_SECTION_IDS,
    REFLECT_CONTEXT_SECTION_IDS,
    build_plan_context_block,
    build_reflect_context_block,
    default_context_budget,
    select_plan_context,
    select_reflect_context,
    _render_goal_agenda,
)
from phone_agent.graph.goal import CriterionSpec, GoalContract
from phone_agent.graph.goal_evidence import ever_matched
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.graph.predicates import PredicateSpec

CONTRACT_ID = "unbound-runtime-contract"


@dataclass
class FakeModelResponse:
    thinking: str
    action: str


class FakeModelClient:
    def __init__(self, response: FakeModelResponse) -> None:
        self.response = response
        self.messages = None
        self.calls = 0

    def request(self, messages, **kwargs):
        self.messages = messages
        self.calls += 1
        return self.response


def _entry(
    status: str,
    *,
    epoch: int,
    criterion: str = "criterion_a",
    screen_id: str = "screen-A",
    target_app_entered: bool = True,
    contract_id: str = CONTRACT_ID,
) -> dict:
    return {
        "contract_id": contract_id,
        "criterion_id": criterion,
        "status": status,
        "screen_id": screen_id,
        "observation_epoch": epoch,
        "target_app_entered": target_app_entered,
        "reason_code": "test",
    }


def test_latch_keeps_satisfied_across_transient_unknown() -> None:
    """matched at epoch 2, then unknown on the next observation -> stays latched."""

    latch = ever_matched(
        [
            _entry("matched", epoch=2, screen_id="screen-A"),
            _entry("unknown", epoch=3, screen_id="screen-A"),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is True
    assert latch.matched_epoch == 2
    assert latch.matched_screen_id == "screen-A"


def test_latch_keeps_across_screen_change_stale() -> None:
    """matched on screen-A, then stale because the current screen differs -> stays."""

    latch = ever_matched(
        [
            _entry("matched", epoch=2, screen_id="screen-A"),
            _entry("stale", epoch=3, screen_id="screen-B"),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is True
    assert latch.matched_epoch == 2


def test_latch_unlocks_on_deterministic_contradiction() -> None:
    """contradicted (positive counter-observation) after matched -> unlock."""

    latch = ever_matched(
        [
            _entry("matched", epoch=2, screen_id="screen-A"),
            _entry("contradicted", epoch=3, screen_id="screen-B"),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is False
    assert latch.matched_epoch is None


def test_latch_relatches_after_positive_reobservation() -> None:
    """contradicted then a fresh qualified matched -> re-latch."""

    latch = ever_matched(
        [
            _entry("matched", epoch=2, screen_id="screen-A"),
            _entry("contradicted", epoch=3, screen_id="screen-B"),
            _entry("matched", epoch=4, screen_id="screen-B"),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is True
    assert latch.matched_epoch == 4


def test_latch_requires_target_app_entry_gate() -> None:
    """A matched entry observed before target-app entry must not pin."""

    latch = ever_matched(
        [_entry("matched", epoch=2, target_app_entered=False)],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is False


def test_latch_ungated_match_does_not_reset_prior_latch() -> None:
    """A later matched entry without the gate neither pins nor unpins."""

    latch = ever_matched(
        [
            _entry("matched", epoch=2, screen_id="screen-A", target_app_entered=True),
            _entry("matched", epoch=3, screen_id="screen-B", target_app_entered=False),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is True
    assert latch.matched_epoch == 2


def test_latch_ignores_other_contracts_and_criteria() -> None:
    latch = ever_matched(
        [
            _entry("matched", epoch=2, criterion="other_criterion"),
            _entry("matched", epoch=2, contract_id="other-contract"),
            _entry("unknown", epoch=3, criterion="criterion_a"),
        ],
        criterion_id="criterion_a",
        contract_id=CONTRACT_ID,
    )
    assert latch.latched is False


def test_render_goal_agenda_marks_latched_item() -> None:
    rendered = _render_goal_agenda(
        [
            {
                "description": "打开小红书",
                "status": "satisfied",
                "verification": "app_or_activity_match",
                "predicate_id": "app.foreground_identity",
                "latched": True,
                "latched_epoch": 7,
            },
            {
                "description": "搜索博主",
                "status": "unknown",
                "verification": "vlm_judge",
                "predicate_id": None,
            },
        ],
        lang="cn",
        consumer="inject",
        task_context=None,
    )
    assert "已满足: 打开小红书(app.foreground_identity, 曾观察于 epoch 7)" in rendered
    assert "未满足: 搜索博主(vlm_judge, 待验收)" in rendered

    rendered_en = _render_goal_agenda(
        [
            {
                "description": "open app",
                "status": "satisfied",
                "verification": "app_or_activity_match",
                "predicate_id": "app.foreground_identity",
                "latched": True,
                "latched_epoch": 7,
            }
        ],
        lang="en",
        consumer="inject",
        task_context=None,
    )
    assert "Satisfied: open app(app.foreground_identity, observed at epoch 7)" in rendered_en


def test_plan_block_omits_summarized_history() -> None:
    state = {
        "summarized_history": "step=1 action=Tap target=ax_1 success=True",
        "gui_memory": {
            "tried_actions": [],
            "visited_screens": [],
            "scroll_memory": {},
            "task_progress": {"last_verdict": "succeeded"},
        },
        "context_budget": default_context_budget(),
    }
    block, _metrics = build_plan_context_block(state)
    assert "summarized_history" not in block
    assert "target=ax_1" not in block
    assert "summarized_history" not in CONTEXT_SECTION_IDS
    assert "summarized_history" not in REFLECT_CONTEXT_SECTION_IDS


def test_plan_selection_excludes_summarized_history() -> None:
    result = select_plan_context(
        {
            "goal_agenda": [
                {
                    "description": "任务目标",
                    "status": "unknown",
                    "verification": "vlm_judge",
                    "predicate_id": None,
                }
            ],
            "summarized_history": "step=1 action=Tap success=True",
            "context_budget": default_context_budget(),
        },
        mode="inject",
        lang="cn",
    )
    assert "goal_agenda" in result.selected_sections
    assert "summarized_history" not in result.selected_sections
    assert "summarized_history" not in result.context_block


def test_reflect_block_omits_summarized_history() -> None:
    result = select_reflect_context(
        {
            "screen_belief": {
                "summary": "页面加载完成",
                "current_app": "com.example",
                "confidence": "high",
            },
            "summarized_history": "step=1 action=Tap success=True",
            "context_budget": default_context_budget(),
        },
        mode="inject",
        lang="cn",
    )
    assert "summarized_history" not in result.selected_sections
    block, _metrics = build_reflect_context_block(
        {
            "screen_belief": {
                "summary": "页面加载完成",
                "current_app": "com.example",
                "confidence": "high",
            },
            "summarized_history": "step=1 action=Tap success=True",
            "context_budget": default_context_budget(),
        },
        lang="cn",
    )
    assert "summarized_history" not in block


def test_latched_agenda_line_survives_context_budget() -> None:
    """The lock line must not be squeezed out by pending-acceptance rows."""

    agenda = [
        {
            "description": f"已完成子目标 {index}",
            "status": "satisfied",
            "verification": "app_or_activity_match",
            "predicate_id": "app.foreground_identity",
            "latched": True,
            "latched_epoch": index + 1,
        }
        for index in range(3)
    ] + [
        {
            "description": f"待验收子目标 {index} 需要模型确认画面内容",
            "status": "unknown",
            "verification": "vlm_judge",
            "predicate_id": None,
        }
        for index in range(4)
    ]
    tried = [
        {
            "step_count": index,
            "action": "Tap",
            "mark_id": f"ax_{index}",
            "target_center": [100 + index, 200 + index],
            "surface": "com.xhs/FeedActivity",
            "result_success": True,
            "failure_cause": None,
        }
        for index in range(12)
    ]
    state = {
        "goal_agenda": agenda,
        "gui_memory": {
            "tried_actions": tried,
            "visited_screens": [
                {
                    "screen_id": f"screen-{index}",
                    "current_app": "com.xhs",
                    "step_count": index,
                }
                for index in range(12)
            ],
            "scroll_memory": {},
            "task_progress": {"last_verdict": "succeeded"},
        },
        "failure_memory": [
            {
                "step_count": index,
                "action": "Tap",
                "current_app": "com.xhs",
                "failure_cause": "element_not_found",
                "suggested_strategy": "retry and re-locate the target via accessibility tree",
            }
            for index in range(5)
        ],
        "grounding_observation": {
            "provider": "accessibility",
            "candidates": [
                {"mark_id": f"accessibility_mark_{index}", "role": "TextView"}
                for index in range(40)
            ],
        },
        "context_budget": default_context_budget(),
    }

    block, metrics = build_plan_context_block(state)
    # The other sections plus the agenda overflow the block budget; the trim must
    # cut from the tail (never into the milestone agenda).
    assert metrics["context_truncated"] is True
    assert "曾观察于 epoch 1" in block
    assert "曾观察于 epoch 3" in block
    # The last agenda row (pending-acceptance tail of the agenda section) survives.
    assert "待验收子目标 3" in block
    # And the block-level trim must not drop the agenda head.
    assert block.index("goal_agenda") < block.index("failure_memory")


def _latch_contract() -> GoalContract:
    return GoalContract(
        task_hash="h",
        redacted_objective="打开目标应用",
        objective_length=6,
        success_criteria=[
            CriterionSpec(
                name="target_app_open",
                description="目标应用已打开",
                verification="app_or_activity_match",
                required=True,
                predicate=PredicateSpec(
                    predicate_id="app.foreground_identity",
                    expected_value="com.example.target",
                    matcher_id="casefold_exact",
                    privacy_class="identifier",
                ),
            ),
        ],
        target_app_hint="目标应用",
        verification_strategy="app_or_activity_at_finish",
        compile_status="compiled",
        compile_source="external",
    )


def test_reflect_node_keeps_goal_agenda_satisfied_from_model_observation(
    base_state, fake_device
) -> None:
    """Wiring: reflect writes satisfied+latched when the criterion was read by
    the model on an earlier screen (model_observation ledger entry)."""

    base_state["goal_contract"] = _latch_contract()
    base_state["goal_contract_status"] = "compiled"
    # An earlier model screen-read observed the criterion.
    base_state["goal_evidence_ledger"] = [
        {
            "kind": "model_observation",
            "contract_id": CONTRACT_ID,
            "criterion": "target_app_open",
            "status": "observed",
            "observed_value": "目标应用",
            "step": 5,
            "screen_id": "screen-old",
            "observation_epoch": 5,
        }
    ]
    model = FakeModelClient(
        FakeModelResponse(
            "ok",
            '{"verdict":"failed","failure_cause":"unknown","suggested_strategy":"retry","message":"not sure"}',
        )
    )

    result = reflect_node(
        base_state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "grounding_provider_name": "off",
            }
        },
    )

    agenda = result.get("goal_agenda") or []
    assert agenda, "goal_agenda must be written when a contract is present"
    item = next(
        item
        for item in agenda
        if item.get("predicate_id") == "app.foreground_identity"
    )
    assert item["status"] == "satisfied"
    assert item.get("latched") is True
    assert item.get("latched_epoch") == 5
    # The current step's own (observation-less) reflect pass must not break it.
    observations = [
        e
        for e in result["goal_evidence_ledger"]
        if e.get("kind") == "model_observation"
    ]
    assert observations and observations[0]["status"] == "observed"
