"""Model-Delegated Evidence (execution-model-delegated-evidence.md).

S1 全链路: reflect → state (criterion_gap_list channel) → plan rendering.
S7 regression fixtures: the run-G shape — panel observation入账 → 缺口清单 ✅
→ finish judge with trajectory summary + evidence reference → pass; and the
negative (no observation + reference-less judge → rejected).

All fixtures use synthetic (redacted) data; no real screenshots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from phone_agent.graph.goal import GoalContract, SuccessCriterion, TaskStage
from phone_agent.graph.goal_evidence import (
    append_model_observations,
    bounded_evidence_ledger,
    criterion_gap_status,
    latest_model_observation,
    model_observation_entry,
)
from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.reflect import (
    parse_reflection_action,
    reflect_node,
)


@dataclass
class _FakeModelResponse:
    thinking: str
    action: str


class _FakeModelClient:
    def __init__(self, response: _FakeModelResponse | list[_FakeModelResponse]) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.messages: list[dict] | None = None
        self.calls = 0

    def request(self, messages, **kwargs):
        self.messages = messages
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def _run_g_contract() -> GoalContract:
    """run-G shape: a confirmed departure-time criterion + a results criterion."""
    return GoalContract(
        task_hash="run-g",
        redacted_objective="查机票",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "departure_time",
                "筛选面板显示‘06:00-12:00’时段",
                "vlm_judge",
                provenance="confirmed",
                control_hint="筛选面板",
                required=True,
            ),
            SuccessCriterion(
                "flight_results",
                "航班列表卡片",
                "vlm_judge",
                required=True,
            ),
        ],
        task_plan=(
            TaskStage("S1", "应用筛选", ("departure_time",), "", 0),
            TaskStage("S2", "结果页", ("flight_results",), "", 1),
        ),
        compile_status="compiled",
        compile_source="external",
    )


def _reflect_config(fake_device, model) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "verbose": False,
            "grounding_provider_name": "off",
        }
    }


def _plan_config(fake_device, model) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "verbose": False,
            "screen_marks": [
                {
                    "mark_id": "ax_panel",
                    "bbox": [50, 100, 900, 300],
                    "role": "TextView",
                    "text_summary": "筛选面板 06:00-12:00",
                }
            ],
            "grounding_provider_name": "off",
        }
    }


def _plan_text(model: _FakeModelClient) -> str:
    """All text parts of the last plan request the model received."""
    parts: list[str] = []
    for message in model.messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        for item in content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


# ----------------------------------------------------------------------
# S1: reflect → state → plan 全链路 (the previously-missing wiring test)
# ----------------------------------------------------------------------


def test_reflect_state_plan_gap_list_full_chain(base_state, fake_device) -> None:
    """reflect writes criteria_observations into the ledger; the declared
    criterion_gap_list channel carries the fold into state; the plan prompt
    renders the gap list with the observed criterion satisfied.

    This is the chain that used to break silently: AgentState did not declare
    the channel, so LangGraph dropped the reflect return and plan always read
    an empty gap list.
    """
    base_state["goal_contract"] = _run_g_contract()
    base_state["task"] = "查机票"
    base_state["step_count"] = 15
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
    }
    base_state["action_result"] = {
        "success": True,
        "should_finish": False,
        "message": "ok",
    }
    reflect_model = _FakeModelClient(
        _FakeModelResponse(
            "ok",
            json.dumps(
                {
                    "verdict": "succeeded",
                    "failure_cause": "none",
                    "suggested_strategy": "continue",
                    "message": "筛选面板已打开",
                    "criteria_observations": [
                        {
                            "criterion": "departure_time",
                            "status": "observed",
                            "observed_value": "06:00-12:00",
                        },
                        {"criterion": "flight_results", "status": "not_visible"},
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )
    reflected = reflect_node(base_state, _reflect_config(fake_device, reflect_model))

    # S1: the model screen-read lands in the ledger (redacted form).
    observations = [
        e
        for e in reflected["goal_evidence_ledger"]
        if e.get("kind") == "model_observation"
    ]
    by_criterion = {e["criterion"]: e for e in observations}
    assert by_criterion["departure_time"]["status"] == "observed"
    assert by_criterion["departure_time"]["observed_value"] == "06:00-12:00"
    assert by_criterion["flight_results"]["status"] == "not_visible"
    assert all(
        e.get("step") == 15 and e.get("screen_id") is not None
        for e in observations
    )

    # The gap list fold marks the observed criterion satisfied: the current
    # stage is S2 (flight_results pending) and the sealed S1 row carries
    # departure_time (✅).
    gap = reflected["criterion_gap_list"]
    assert gap is not None
    assert gap["current_stage_id"] == "S2"
    assert any(
        r["name"] == "departure_time" and r["stage_id"] == "S1"
        for r in gap["sealed"]
    )
    item = next(i for i in gap["items"] if i["name"] == "flight_results")
    assert item["status"] == "pending"

    # Simulate LangGraph applying the reflect channel writes to state (the
    # criterion_gap_list channel is now declared in AgentState).
    merged_state = {**base_state, **reflected}
    assert merged_state["criterion_gap_list"]["items"] is not None

    # Plan consumes the gap list: the prompt must render the sealed ✅ row and
    # the pending ⏳ row.
    plan_model = _FakeModelClient(
        _FakeModelResponse("", '{"type":"do","action":"tap","element":[1,2]}')
    )
    plan_result = plan_node(merged_state, _plan_config(fake_device, plan_model))
    assert plan_result is not None
    plan_text = _plan_text(plan_model)
    assert "判据缺口清单" in plan_text
    assert "✅ departure_time（S1 已确认）" in plan_text
    assert "⏳ flight_results" in plan_text


# ----------------------------------------------------------------------
# S1: model_observation entry form (redaction, bounded ledger)
# ----------------------------------------------------------------------


def test_model_observation_entry_is_redacted_and_bounded() -> None:
    entry = model_observation_entry(
        contract_id="c1",
        criterion="departure_time",
        status="observed",
        observed_value="联系 13800138000 06:00-12:00",
        step=15,
        screen_id="s1",
        observation_epoch=5,
    )
    assert entry["kind"] == "model_observation"
    assert "13800138000" not in entry["observed_value"]
    assert entry["observed_value"] is not None

    # Bounded: old observations crop out, latest kept.
    ledger = []
    for i in range(120):
        ledger = append_model_observations(
            ledger,
            contract_id="c1",
            observations=[{"criterion": "c", "status": "observed", "observed_value": f"v{i}"}],
            step=i,
            screen_id=f"s{i}",
            observation_epoch=i,
        )
    latest = latest_model_observation(ledger, contract_id="c1", criterion="c")
    assert latest["step"] == 119
    assert len(
        [e for e in ledger if e.get("kind") == "model_observation"]
    ) <= 48


def test_parse_reflection_action_captures_criteria_observations() -> None:
    parsed = parse_reflection_action(
        json.dumps(
            {
                "verdict": "succeeded",
                "failure_cause": "none",
                "suggested_strategy": "continue",
                "message": "ok",
                "criteria_observations": [
                    {"criterion": "a", "status": "observed", "observed_value": "x"},
                    {"criterion": "b", "status": "not_visible"},
                    {"criterion": "c", "status": "bogus"},
                    "not-a-dict",
                ],
            }
        )
    )
    assert parsed.criteria_observations == [
        {"criterion": "a", "status": "observed", "observed_value": "x"},
        {"criterion": "b", "status": "not_visible"},
    ]


# ----------------------------------------------------------------------
# S7: run-G regression fixtures
# ----------------------------------------------------------------------


def test_run_g_panel_observation_to_finish_passes(base_state, fake_device) -> None:
    """run-G positive: s15 面板观察 departure_time=06:00-12:00 入账 → 缺口清单
    ✅ → finish judge 带轨迹摘要+evidence_step 引用 → 通过."""
    from phone_agent.graph.nodes.acceptance import acceptance_node

    from phone_agent.graph.goal_evidence import seal_satisfied_stages

    contract = _run_g_contract()
    base_state["goal_contract"] = contract
    base_state["task"] = "查机票"
    base_state["step_count"] = 15
    base_state["expected_outcome"] = None
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": ["departure_time", "flight_results"],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "ok"}
    base_state["pending_finish"] = True
    ledger = append_model_observations(
        [],
        contract_id="unbound-runtime-contract",
        observations=[
            {
                "criterion": "departure_time",
                "status": "observed",
                "observed_value": "06:00-12:00",
            }
        ],
        step=15,
        screen_id="panel",
        observation_epoch=15,
    )
    # The panel observation seals stage S1 (all done criteria observed).
    ledger, seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id="unbound-runtime-contract",
        screen_id="panel",
        step=15,
    )
    assert [s["stage_id"] for s in seals] == ["S1"]
    base_state["goal_evidence_ledger"] = ledger
    gap = criterion_gap_status(
        contract=contract,
        ledger=ledger,
        contract_id="unbound-runtime-contract",
        screen_id="panel",
        observation_epoch=15,
    )
    assert gap is not None
    assert gap["current_stage_id"] == "S2"
    assert any(
        r["name"] == "departure_time" and r["stage_id"] == "S1"
        for r in gap["sealed"]
    )

    judge_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "flight_results",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        }
                    ],
                    "message": "done",
                },
                ensure_ascii=False,
            ),
        )
    )
    result = acceptance_node(
        base_state,
        {
            "configurable": {
                "model_client": judge_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "ax_results",
                        "bbox": [50, 100, 900, 200],
                        "role": "TextView",
                        "text_summary": "航班列表",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )
    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    # The judge prompt carried the trajectory summary (S3 causality source).
    parts: list[str] = []
    for message in judge_model.messages or []:
        for item in (message.get("content") or []) if isinstance(message, dict) else []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    prompt_text = "\n".join(parts)
    assert "轨迹摘要" in prompt_text
    assert "s15" in prompt_text
    assert "departure_time=06:00-12:00" in prompt_text


def test_run_g_no_observation_no_reference_rejected(base_state, fake_device) -> None:
    """run-G negative: 无模型观察 + judge 无 evidence_step 引用 → 拒绝
    (fail-closed; unknown never becomes success)."""
    from phone_agent.graph.nodes.acceptance import acceptance_node

    contract = _run_g_contract()
    base_state["goal_contract"] = contract
    base_state["task"] = "查机票"
    base_state["step_count"] = 15
    base_state["expected_outcome"] = None
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": ["departure_time", "flight_results"],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "ok"}
    base_state["pending_finish"] = True
    base_state["goal_evidence_ledger"] = []

    judge_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "departure_time",
                            "status": "satisfied",
                            "observed_value": "航班 06:05 起飞",
                        },
                        {"criterion": "flight_results", "status": "satisfied"},
                    ],
                    "message": "done",
                },
                ensure_ascii=False,
            ),
        )
    )
    result = acceptance_node(
        base_state,
        {
            "configurable": {
                "model_client": judge_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "ax_results",
                        "bbox": [50, 100, 900, 200],
                        "role": "TextView",
                        "text_summary": "航班列表",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )
    assert result["finished"] is False
    assert result["failure_cause"] == "goal_not_satisfied"
    per_criterion = result["finish_validation_evidence"]["evidence"]["per_criterion"]
    assert per_criterion["departure_time"]["status"] == "unknown"
    assert (
        per_criterion["departure_time"]["reason"]
        == "judge_reference_missing_or_out_of_range"
    )


def test_bounded_ledger_keeps_observation_window() -> None:
    ledger = []
    for i in range(10):
        ledger = append_model_observations(
            ledger,
            contract_id="c1",
            observations=[{"criterion": "c", "status": "observed", "observed_value": "v"}],
            step=i,
            screen_id=f"s{i}",
            observation_epoch=i,
        )
    bounded = bounded_evidence_ledger(
        list(ledger), observation_limit=3
    )
    observations = [e for e in bounded if e.get("kind") == "model_observation"]
    assert len(observations) == 3
    assert [e["step"] for e in observations] == [7, 8, 9]


# ----------------------------------------------------------------------
# S7b: 残留启动 fixture replay (form 4) — 首屏即满足部分判据
# ----------------------------------------------------------------------


def test_residual_first_screen_observation_finishes_with_trajectory(
    base_state, fake_device
) -> None:
    """残留启动正例：上一轮留下的筛选状态使首屏（step 1）即满足 departure_time
    判据（面板观察在 s1 入账并封缄 S1）；finish 时 judge 对终局判据带
    evidence_step 引用 → 通过；judge 提示词携带轨迹摘要（s1 观察）判因果。"""
    from phone_agent.graph.goal_evidence import seal_satisfied_stages
    from phone_agent.graph.nodes.acceptance import acceptance_node

    contract = _run_g_contract()
    base_state["goal_contract"] = contract
    base_state["task"] = "查机票"
    base_state["step_count"] = 1
    base_state["expected_outcome"] = None
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": ["departure_time", "flight_results"],
    }
    base_state["action_result"] = {
        "success": True,
        "should_finish": False,
        "message": "ok",
    }
    base_state["pending_finish"] = True
    ledger = append_model_observations(
        [],
        contract_id="unbound-runtime-contract",
        observations=[
            {
                "criterion": "departure_time",
                "status": "observed",
                "observed_value": "06:00-12:00",
            }
        ],
        step=1,
        screen_id="panel",
        observation_epoch=1,
    )
    ledger, seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id="unbound-runtime-contract",
        screen_id="panel",
        step=1,
    )
    assert [s["stage_id"] for s in seals] == ["S1"]
    base_state["goal_evidence_ledger"] = ledger
    gap = criterion_gap_status(
        contract=contract,
        ledger=ledger,
        contract_id="unbound-runtime-contract",
        screen_id="panel",
        observation_epoch=1,
    )
    assert gap is not None
    assert gap["current_stage_id"] == "S2"
    assert any(
        r["name"] == "departure_time" and r["stage_id"] == "S1"
        for r in gap["sealed"]
    )

    judge_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "flight_results",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        }
                    ],
                    "message": "done",
                },
                ensure_ascii=False,
            ),
        )
    )
    result = acceptance_node(
        base_state,
        {
            "configurable": {
                "model_client": judge_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "ax_results",
                        "bbox": [50, 100, 900, 200],
                        "role": "TextView",
                        "text_summary": "航班列表",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )
    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    parts: list[str] = []
    for message in judge_model.messages or []:
        for item in (message.get("content") or []) if isinstance(message, dict) else []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    prompt_text = "\n".join(parts)
    assert "轨迹摘要" in prompt_text
    assert "s1" in prompt_text
    assert "departure_time=06:00-12:00" in prompt_text


def test_residual_first_screen_observation_judge_without_reference_blocked(
    base_state, fake_device
) -> None:
    """残留启动反例：首屏观察已入账，但 judge 对终局判据不带 evidence_step
    引用（自证）→ finish 被阻断（fail-closed，引用形式校验）。"""
    from phone_agent.graph.goal_evidence import seal_satisfied_stages
    from phone_agent.graph.nodes.acceptance import acceptance_node

    contract = _run_g_contract()
    base_state["goal_contract"] = contract
    base_state["task"] = "查机票"
    base_state["step_count"] = 1
    base_state["expected_outcome"] = None
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": ["departure_time", "flight_results"],
    }
    base_state["action_result"] = {
        "success": True,
        "should_finish": False,
        "message": "ok",
    }
    base_state["pending_finish"] = True
    ledger = append_model_observations(
        [],
        contract_id="unbound-runtime-contract",
        observations=[
            {
                "criterion": "departure_time",
                "status": "observed",
                "observed_value": "06:00-12:00",
            }
        ],
        step=1,
        screen_id="panel",
        observation_epoch=1,
    )
    ledger, seals = seal_satisfied_stages(
        ledger,
        contract=contract,
        contract_id="unbound-runtime-contract",
        screen_id="panel",
        step=1,
    )
    assert [s["stage_id"] for s in seals] == ["S1"]
    base_state["goal_evidence_ledger"] = ledger

    judge_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "departure_time",
                            "status": "satisfied",
                            "observed_value": "航班 06:05 起飞",
                        },
                        {"criterion": "flight_results", "status": "satisfied"},
                    ],
                    "message": "done",
                },
                ensure_ascii=False,
            ),
        )
    )
    result = acceptance_node(
        base_state,
        {
            "configurable": {
                "model_client": judge_model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "ax_results",
                        "bbox": [50, 100, 900, 200],
                        "role": "TextView",
                        "text_summary": "航班列表",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )
    assert result["finished"] is False
    assert result["failure_cause"] == "goal_not_satisfied"
    per_criterion = result["finish_validation_evidence"]["evidence"]["per_criterion"]
    assert per_criterion["flight_results"]["status"] == "unknown"
    assert (
        per_criterion["flight_results"]["reason"]
        == "judge_reference_missing_or_out_of_range"
    )
