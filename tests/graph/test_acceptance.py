"""Acceptance node: terminal goal verification, split out of Reflect."""

from phone_agent.graph.edges import after_acceptance, after_execute
from phone_agent.graph.nodes.acceptance import acceptance_node
from phone_agent.graph.nodes.acceptance import (
    _hard_veto,
    _needs_semantic_judgement,
    parse_acceptance_response,
)
from phone_agent.graph.goal_compiler import HeuristicGoalCompiler


# ----------------------------------------------------------------------
# Routing: finish claims go to acceptance, not action reflection
# ----------------------------------------------------------------------


def _execute_state(**overrides) -> dict:
    state = {
        "finished": False,
        "error": None,
        "pending_interrupt": None,
        "action_parsed": {"_metadata": "finish", "message": "done"},
        "pending_finish": True,
        "step_count": 3,
        "max_steps": 20,
    }
    state.update(overrides)
    return state


def test_finish_claim_routes_to_acceptance_not_reflect() -> None:
    assert after_execute(_execute_state()) == "acceptance"


def test_acceptance_success_ends_the_run() -> None:
    assert after_acceptance(_execute_state(finished=True)) == "end"


def test_acceptance_rejection_returns_to_planning() -> None:
    """A rejected claim keeps working rather than ending the run."""
    assert after_acceptance(_execute_state(finished=False)) == "replan"


def test_acceptance_escalation_routes_to_takeover() -> None:
    assert (
        after_acceptance(_execute_state(pending_interrupt="takeover")) == "takeover"
    )


def test_acceptance_respects_step_budget() -> None:
    assert after_acceptance(_execute_state(step_count=20, max_steps=20)) == "end"


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------


def test_parse_acceptance_response_extracts_evidence() -> None:
    completed, message, evidence = parse_acceptance_response(
        '{"completed":true,"message":"done","named_evidence":'
        '[{"criterion":"topic","screen_reference":"mark_id=3",'
        '"observed_value":"周杰伦"}]}'
    )
    assert completed is True
    assert message == "done"
    assert evidence == [
        {
            "criterion": "topic",
            "screen_reference": "mark_id=3",
            "observed_value": "周杰伦",
        }
    ]


def test_parse_acceptance_response_distinguishes_absent_from_empty() -> None:
    """None means "never asked" (fail-closed unknown); [] means "asked, saw
    nothing". The evaluator treats these differently, so parsing must too."""
    _, _, missing = parse_acceptance_response('{"completed":false}')
    assert missing is None

    _, _, empty = parse_acceptance_response(
        '{"completed":false,"named_evidence":[]}'
    )
    assert empty == []


def test_parse_acceptance_response_survives_garbage() -> None:
    completed, _, evidence = parse_acceptance_response("not json at all")
    assert completed is False
    assert evidence is None


# ----------------------------------------------------------------------
# Layer 1: hard veto from collected facts
# ----------------------------------------------------------------------


def test_hard_veto_lists_contradicted_required_criteria() -> None:
    contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩搜索周杰伦")
    collected = {
        "target_app_visible": {"status": "contradicted"},
        "task_completed": {"status": "matched"},
    }
    assert _hard_veto(collected, contract) == ["target_app_visible"]


def test_hard_veto_ignores_absent_and_unknown_evidence() -> None:
    """Absence is not counter-evidence — only an actual contradiction vetoes."""
    contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩搜索周杰伦")
    for status in ("unknown", "unobserved", "missing", "matched"):
        collected = {name: {"status": status} for name in ("target_app_visible",)}
        assert _hard_veto(collected, contract) == []
    assert _hard_veto(None, contract) == []


# ----------------------------------------------------------------------
# Layer 3: the model is consulted only where it is actually needed
# ----------------------------------------------------------------------


def test_semantic_judgement_required_for_raw_text_criteria() -> None:
    contract = HeuristicGoalCompiler().compile(task="在哔哩哔哩搜索周杰伦")
    assert _needs_semantic_judgement(contract)


def test_semantic_judgement_skipped_when_every_criterion_is_structural() -> None:
    """A purely structural contract needs no model call at all."""
    from dataclasses import replace

    contract = HeuristicGoalCompiler().compile(task="关闭蓝牙")
    structural = replace(
        contract,
        success_criteria=[
            item
            for item in contract.success_criteria
            if item.verification == "toggle_state_match"
        ],
    )
    assert structural.success_criteria
    assert not _needs_semantic_judgement(structural)


def test_reflect_no_longer_owns_goal_evaluation() -> None:
    """The split is real: reflect must not import the finish-gate machinery."""
    import phone_agent.graph.nodes.reflect as reflect_module

    for attribute in (
        "evaluate_finish_claim",
        "pure_goal_evaluator",
        "FactCollector",
        "append_evaluation_entries",
    ):
        assert not hasattr(reflect_module, attribute), attribute


# ----------------------------------------------------------------------
# 2.1 Budget-forced acceptance: should_continue routes here at max_steps
# ----------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class _FakeModelResponse:
    thinking: str
    action: str


class _FakeModelClient:
    def __init__(self, response: _FakeModelResponse) -> None:
        self.response = response

    def request(self, messages, **kwargs):
        return self.response


def _budget_state(**overrides) -> dict:
    state = {
        "task": "测试任务",
        "goal_contract": HeuristicGoalCompiler().compile(task="测试任务"),
        "goal_contract_status": "compiled",
        "lang": "cn",
        "step_count": 20,
        "max_steps": 20,
        "finished": False,
        "error": None,
        "pending_finish": False,
        "budget_acceptance_done": False,
        "action_parsed": {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        "action_result": {"success": True, "should_finish": False, "message": "ok"},
        "observation_retry_count": 0,
        "acceptance_round_count": 0,
        "context_mode": "inject",
        "screen_belief": {},
        "goal_evidence_ledger": [],
        "expected_outcome": None,
        "failure_memory": [],
        "summarized_history": "",
        "gui_memory": {},
    }
    state.update(overrides)
    return state


def test_budget_forced_acceptance_marks_flags_and_fails_closed_without_evidence(
    base_state, fake_device
) -> None:
    """2.1: at max_steps without a model claim, acceptance still runs the full
    authority stack; with no evidence the judge rejection attributes
    goal_not_satisfied instead of unknown."""
    model = _FakeModelClient(
        _FakeModelResponse("ok", '{"completed":false,"message":"任务未完成"}')
    )
    state = _budget_state()

    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "tab",
                        "bbox": [0, 0, 1000, 100],
                        "role": "TextView",
                        "text_summary": "首页",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["budget_acceptance_done"] is True
    assert result["finish_source"] == "budget_forced"
    assert result["pending_finish"] is True
    assert result["finished"] is False
    assert result["failure_cause"] == "goal_not_satisfied"
    assert result["finish_validation_status"] != "success"
    # after_acceptance still routes to end at max_steps (edges are pure, so
    # the merged state is simulated), so the run terminates with a real
    # attribution instead of unknown.
    assert after_acceptance({**state, **result}) == "end"


def test_budget_forced_acceptance_model_can_recognize_actual_completion(
    base_state, fake_device
) -> None:
    """2.1: the semantic judge may recognize the task really completed (e.g.
    the target content is already open) and open the finish gate."""
    model = _FakeModelClient(
        _FakeModelResponse(
            "ok",
            '{"completed":true,"message":"已完成",'
            '"named_evidence":[{"criterion":"task_completed",'
            '"screen_reference":"mark_id=done","observed_value":"测试任务"}]}',
        )
    )
    state = _budget_state()

    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "done",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "测试任务",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["budget_acceptance_done"] is True
    assert result["finish_source"] == "budget_forced"
    assert result["finished"] is True
    assert result["failure_cause"] is None
    assert result["finish_validation_status"] == "success"


def test_budget_flags_not_set_for_model_finish_claim_channel(
    base_state, fake_device
) -> None:
    """A model finish claim at max_steps is not budget-forced: the flag stays
    unset so a later rejection could still trigger the forced channel."""
    model = _FakeModelClient(
        _FakeModelResponse("ok", '{"completed":false,"message":"未完成"}')
    )
    state = _budget_state(pending_finish=True)

    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "tab",
                        "bbox": [0, 0, 1000, 100],
                        "role": "TextView",
                        "text_summary": "首页",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    # The node did not claim the budget channel: no flags in the update, so
    # the merged state keeps the incoming False / None values.
    assert result.get("budget_acceptance_done") is None
    assert result.get("finish_source") is None
    assert result["pending_finish"] is False


# ----------------------------------------------------------------------
# W1-A: judge prompt whitelist, normalization, and reply traceability
# ----------------------------------------------------------------------


def test_judge_prompt_cn_declares_verbatim_whitelist_rules() -> None:
    from phone_agent.graph.nodes.acceptance import ACCEPTANCE_SYSTEM_PROMPT_CN

    assert "标准名白名单" in ACCEPTANCE_SYSTEM_PROMPT_CN
    assert "逐字等于" in ACCEPTANCE_SYSTEM_PROMPT_CN
    assert "禁止改写、翻译、大小写变化" in ACCEPTANCE_SYSTEM_PROMPT_CN
    assert "缺一不可" in ACCEPTANCE_SYSTEM_PROMPT_CN
    assert "输出 completed=false" in ACCEPTANCE_SYSTEM_PROMPT_CN


def test_judge_prompt_en_declares_verbatim_whitelist_rules() -> None:
    from phone_agent.graph.nodes.acceptance import ACCEPTANCE_SYSTEM_PROMPT_EN

    assert "criterion name whitelist" in ACCEPTANCE_SYSTEM_PROMPT_EN
    assert "VERBATIM" in ACCEPTANCE_SYSTEM_PROMPT_EN
    assert "exactly one named_evidence" in ACCEPTANCE_SYSTEM_PROMPT_EN
    assert "output completed=false" in ACCEPTANCE_SYSTEM_PROMPT_EN


def test_judge_criterion_names_returns_judge_only_in_contract_order() -> None:
    from phone_agent.graph.goal import GoalContract, SuccessCriterion
    from phone_agent.graph.nodes.acceptance import _judge_criterion_names

    contract = GoalContract(
        task_hash="h",
        redacted_objective="o",
        objective_length=1,
        success_criteria=[
            SuccessCriterion(
                name="auto_c", description="", verification="app_or_activity_match"
            ),
            SuccessCriterion(name="judge_b", description="", verification="vlm_judge"),
            SuccessCriterion(name="judge_a", description="", verification="vlm_judge"),
        ],
        compile_status="compiled",
    )
    assert _judge_criterion_names(contract) == ["judge_b", "judge_a"]


def test_judge_whitelist_block_renders_exact_names_both_langs() -> None:
    from phone_agent.graph.nodes.acceptance import _judge_whitelist_block

    names = ["flight_search_parameters", "cheapest_flight_result"]
    cn = _judge_whitelist_block(names, lang="cn")
    en = _judge_whitelist_block(names, lang="en")
    assert "标准名白名单" in cn
    assert "flight_search_parameters" in cn and "cheapest_flight_result" in cn
    assert "VERBATIM" in en
    assert "flight_search_parameters" in en and "cheapest_flight_result" in en
    assert _judge_whitelist_block([], lang="cn") == ""
    assert _judge_whitelist_block([], lang="en") == ""


class _JudgeScreenshot:
    base64_data: str = "fake-image"
    mime_type: str = "image/png"


class _FakeTraceWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, node, event, step_id, payload) -> None:
        self.events.append(
            {"node": node, "event": event, "step_id": step_id, "payload": payload}
        )


def test_semantic_judge_emits_redacted_reply_trace(base_state) -> None:
    """A3: the judge's raw reply key fields land in the trace payload; text is
    regex-redacted (P0 #10) before egress."""
    from phone_agent.graph.nodes.acceptance import _run_semantic_judge

    model = _FakeModelClient(
        _FakeModelResponse(
            "ok",
            '{"completed":true,"message":"done",'
            '"named_evidence":[{"criterion":"task_completed",'
            '"screen_reference":"mark_id=x","observed_value":"13800138000"}]}',
        )
    )
    writer = _FakeTraceWriter()
    config = {"configurable": {"model_client": model, "trace_writer": writer}}

    verdicts, named_evidence, message = _run_semantic_judge(
        state=base_state,
        config=config,
        lang="cn",
        task=base_state["task"],
        current_app="FakeApp",
        screenshot=_JudgeScreenshot(),
        after_observation_summary={},
    )

    assert named_evidence is not None
    assert message == "done"
    replies = [e for e in writer.events if e["event"] == "acceptance_judge_reply"]
    assert len(replies) == 1
    payload = replies[0]["payload"]
    assert payload["completed"] is True
    assert payload["message"] == "done"
    assert "verdicts" in payload
    assert payload["named_evidence"][0]["criterion"] == "task_completed"
    assert payload["named_evidence"][0]["screen_reference"] == "mark_id=x"
    # A phone number in observed_value must not survive trace egress.
    assert payload["named_evidence"][0]["observed_value"] == "<redacted>"


def test_budget_forced_acceptance_judge_drifted_names_normalize_to_match(
    base_state, fake_device
) -> None:
    """W1-A end-to-end: the judge writes "Task Completed" for criterion
    task_completed; the normalized whitelist lookup still opens the gate."""
    model = _FakeModelClient(
        _FakeModelResponse(
            "ok",
            '{"completed":true,"message":"已完成",'
            '"named_evidence":[{"criterion":"Task Completed",'
            '"screen_reference":"mark_id=done","observed_value":"测试任务"}]}',
        )
    )
    state = _budget_state()

    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "done",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "测试任务",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"


def test_budget_forced_acceptance_judge_wrong_name_stays_fail_closed(
    base_state, fake_device
) -> None:
    """W1-A: a judge name outside the whitelist never satisfies the criterion
    and the gate stays closed."""
    model = _FakeModelClient(
        _FakeModelResponse(
            "ok",
            '{"completed":true,"message":"已完成",'
            '"named_evidence":[{"criterion":"not a real criterion",'
            '"screen_reference":"mark_id=done","observed_value":"x"}]}',
        )
    )
    state = _budget_state()

    result = acceptance_node(
        state,
        {
            "configurable": {
                "model_client": model,
                "device_factory": fake_device,
                "verbose": False,
                "after_screen_marks": [
                    {
                        "mark_id": "done",
                        "bbox": [50, 60, 950, 160],
                        "role": "TextView",
                        "text_summary": "测试任务",
                    }
                ],
                "grounding_provider_name": "off",
            }
        },
    )

    assert result["finished"] is False
    assert result["finish_validation_status"] != "success"
