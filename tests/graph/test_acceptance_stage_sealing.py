"""Stage-Sealing Phase C: acceptance rewrite.

Covers the §11 acceptance requirements that live at the acceptance layer:
per-criterion tri-state fold, seal-presented acceptance (seal 出示即过), L3
judge receiving the bounded ledger digest, structured rejection feedback with
stage ids, legacy-contract compatible parsing, CN/EN judge prompt pairing,
compile-time terminal-literal warnings, and the mandatory end-to-end regression
(calendar screen records the year as L1 → finish passes although the final
screen no longer shows it).
"""

import json
from dataclasses import dataclass

from phone_agent.config.prompts_en import ACCEPTANCE_JUDGE_PROMPT_EN
from phone_agent.config.prompts_zh import ACCEPTANCE_JUDGE_PROMPT_ZH
from phone_agent.graph.goal import GoalContract, SuccessCriterion, TaskStage
from phone_agent.graph.goal_binding import compute_task_binding
from phone_agent.graph.goal_evidence import (
    append_screen_text_digest,
    stage_semantic_key,
    terminal_literal_warnings,
)
from phone_agent.graph.goal_evaluator import fold_acceptance_verdicts
from phone_agent.graph.goal_requirements import TaskRequirementExtractor
from phone_agent.graph.nodes.acceptance import (
    acceptance_node,
    parse_acceptance_response,
    parse_acceptance_verdicts,
)
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG
from phone_agent.graph.runtime_goal import RuntimeGoalContext


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


@dataclass
class _FakeModelResponse:
    thinking: str
    action: str


class _FakeModelClient:
    def __init__(self, response: _FakeModelResponse) -> None:
        self.response = response
        self.messages: list[dict] | None = None
        self.calls = 0

    def request(self, messages, **kwargs):
        self.messages = messages
        self.calls += 1
        return self.response


class _FakeTraceWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, node, event, step_id, payload) -> None:
        self.events.append(
            {"node": node, "event": event, "step_id": step_id, "payload": payload}
        )


def _model_text(model: _FakeModelClient) -> str:
    """All text parts of the last request the model received."""
    parts: list[str] = []
    for message in model.messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        for item in content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Shared fixtures for the acceptance-node tests
# ----------------------------------------------------------------------


FLIGHT_TASK = "订 2026年10月1日的机票"


def _flight_contract(**overrides) -> GoalContract:
    """The §3/§11 scenario: the year literal belongs to stage S1 (calendar
    screen); the results page is a terminal criterion."""
    criteria = [
        SuccessCriterion(
            "date",
            "出发日期 2026年10月1日",
            "vlm_judge",
            predicate=CORE_PREDICATE_CATALOG.create_spec(
                "semantic.entity_matches", "2026年10月1日"
            ),
            freshness="trajectory",
            required=True,
        ),
        SuccessCriterion(
            "flight_results",
            "航班列表卡片",
            "vlm_judge",
            required=True,
        ),
    ]
    return GoalContract(
        task_hash=compute_task_binding(FLIGHT_TASK),
        redacted_objective=FLIGHT_TASK,
        objective_length=len(FLIGHT_TASK),
        success_criteria=criteria,
        entities_sha=list(
            TaskRequirementExtractor().extract(FLIGHT_TASK).target_entity_hashes
        ),
        task_plan=(TaskStage("S1", "选择日期", ("date",), "", 0),),
        compile_status="compiled",
        compile_source="external",
        **overrides,
    )


def _bind_contract(base_state, contract: GoalContract) -> tuple[str, RuntimeGoalContext]:
    """Compile the contract through goal_node exactly like a real run: the
    contract lands in the runtime context and state carries a state-payload
    dict with a runtime reference. Returns (runtime_reference, runtime_goal)."""
    requirements = TaskRequirementExtractor().extract(FLIGHT_TASK)
    runtime_goal = RuntimeGoalContext()
    update = goal_node(
        {
            "task": FLIGHT_TASK,
            "lang": "cn",
            "step_count": 0,
            "goal_contract_status": "pending",
        },
        {
            "configurable": {
                "runtime_goal_context": runtime_goal,
                "task_goal_contract_override": contract,
                "task_requirement_set_override": requirements,
            }
        },
    )
    assert update["goal_contract_status"] == "user_override"
    base_state.update(update)
    reference = base_state["goal_contract"]["runtime_reference"]
    return reference, runtime_goal


def _seed_state(base_state, contract: GoalContract) -> None:
    base_state["task"] = FLIGHT_TASK
    base_state["goal_contract_status"] = "compiled"
    base_state["goal_compile_source"] = "external"
    base_state["action_parsed"] = {
        "_metadata": "finish",
        "message": "done",
        "matched_terminal_evidence": ["date", "flight_results"],
    }
    base_state["action_result"] = {
        "success": True,
        "should_finish": False,
        "message": "done",
    }
    base_state["pending_finish"] = True
    base_state["expected_outcome"] = None


def _acceptance_config(fake_device, model: _FakeModelClient, marks: list[dict]) -> dict:
    return {
        "configurable": {
            "model_client": model,
            "device_factory": fake_device,
            "verbose": False,
            "after_screen_marks": marks,
            "grounding_provider_name": "off",
        }
    }


_RESULTS_MARK = [
    {
        "mark_id": "results",
        "bbox": [50, 100, 900, 200],
        "role": "TextView",
        "text_summary": "航班列表",
    }
]


# ----------------------------------------------------------------------
# §6: judge prompt pairing + verdict parsing
# ----------------------------------------------------------------------


def test_judge_prompts_cn_en_paired_for_stage_sealing() -> None:
    """CN/EN judge prompts move in lockstep: both declare the tri-state
    verdicts contract, both keep legacy completed/named_evidence compatible
    with the new format taking priority, and both announce the ledger digest
    as mechanically extracted fact."""
    for prompt in (ACCEPTANCE_JUDGE_PROMPT_ZH, ACCEPTANCE_JUDGE_PROMPT_EN):
        assert "verdicts" in prompt
        assert "satisfied" in prompt
        assert "unknown" in prompt
        assert "contradicted" in prompt
        assert "named_evidence" in prompt
    assert "账本" in ACCEPTANCE_JUDGE_PROMPT_ZH
    assert "ledger" in ACCEPTANCE_JUDGE_PROMPT_EN
    assert "verdicts 优先" in ACCEPTANCE_JUDGE_PROMPT_ZH
    assert "takes priority" in ACCEPTANCE_JUDGE_PROMPT_EN


def test_parse_acceptance_verdicts_tri_state_and_legacy_compat() -> None:
    raw = json.dumps(
        {
            "verdicts": [
                {"criterion": "a", "status": "satisfied", "observed_value": "x"},
                {"criterion": "b", "status": "unknown"},
                {"criterion": "c", "status": "contradicted", "observed_value": "y"},
                {"criterion": "d", "status": "bogus"},
            ],
            "message": "ok",
        }
    )
    verdicts = parse_acceptance_verdicts(raw)
    assert verdicts is not None
    assert [item["criterion"] for item in verdicts] == ["a", "b", "c"]
    assert verdicts[2]["observed_value"] == "y"

    # New-contract replies with no verdicts field fall back to legacy parsing.
    assert parse_acceptance_verdicts('{"completed":true,"named_evidence":[]}') is None
    # Garbage stays None (fail-closed).
    assert parse_acceptance_verdicts("not json") is None
    assert parse_acceptance_verdicts("[1, 2]") is None

    # Legacy completed/named_evidence still parses (compat path).
    completed, message, named_evidence = parse_acceptance_response(
        '{"completed":true,"message":"done",'
        '"named_evidence":[{"criterion":"a","screen_reference":"mark_id=m1"}]}'
    )
    assert completed is True
    assert message == "done"
    assert named_evidence[0]["criterion"] == "a"


# ----------------------------------------------------------------------
# §5: per-criterion tri-state fold
# ----------------------------------------------------------------------


def test_fold_per_criterion_tri_state() -> None:
    contract = _flight_contract()
    from phone_agent.graph.goal_evidence import append_model_observations

    ledger = append_model_observations(
        [],
        contract_id=contract.task_hash,
        observations=[
            {
                "criterion": "date",
                "status": "observed",
                "observed_value": "2026年10月1日",
            }
        ],
        step=3,
        screen_id="calendar",
        observation_epoch=3,
    )

    # Unknown tier: date observed, terminal criterion not read and no judge
    # reference yet → unknown.
    fold = fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id=contract.task_hash,
        screen_id="results",
        observation_epoch=9,
    )
    assert fold["per_criterion"]["date"]["status"] == "satisfied"
    assert fold["per_criterion"]["date"]["reason"] == "model_observed"
    assert fold["per_criterion"]["flight_results"]["status"] == "unknown"
    assert fold["overall"] == "unknown"

    # Satisfied tier: the judge settles the remaining criterion with a valid
    # evidence reference (S3).
    fold2 = fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id=contract.task_hash,
        screen_id="results",
        observation_epoch=9,
        judge_verdicts=[
            {
                "criterion": "flight_results",
                "status": "satisfied",
                "evidence_step": "final_screen",
            }
        ],
        current_step=9,
    )
    assert fold2["overall"] == "satisfied"
    assert sorted(fold2["satisfied"]) == ["date", "flight_results"]

    # Contradicted tier: a positive counter-observation overrides self-
    # attestation (P0 #13a).
    fold3 = fold_acceptance_verdicts(
        contract=contract,
        ledger=ledger,
        contract_id=contract.task_hash,
        screen_id="results",
        observation_epoch=9,
        judge_verdicts=[
            {
                "criterion": "flight_results",
                "status": "contradicted",
                "evidence_step": "final_screen",
            }
        ],
        current_step=9,
    )
    assert fold3["overall"] == "contradicted"
    assert fold3["contradicted"] == ["flight_results"]


# ----------------------------------------------------------------------
# §4.2/§5: seal presented → acceptance passes without current evidence
# ----------------------------------------------------------------------


def test_acceptance_seal_presented_passes_without_current_evidence(
    base_state, fake_device
) -> None:
    contract = _flight_contract()
    reference, runtime_goal = _bind_contract(base_state, contract)
    stage = contract.task_plan[0]
    criteria = {criterion.name: criterion for criterion in contract.success_criteria}
    ledger = [
        {
            "kind": "stage_seal",
            "contract_id": reference,
            "stage_id": stage.stage_id,
            "criteria_sealed": list(stage.done_criteria),
            "evidence_refs": ["screen-calendar", "step:5"],
            "screen_id": "screen-calendar",
            "step": 5,
            "sealed_at": 5,
            "semantic_key": stage_semantic_key(stage, criteria),
        }
    ]
    _seed_state(base_state, contract)
    base_state["goal_evidence_ledger"] = ledger
    model = _FakeModelClient(
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
                }
            ),
        )
    )
    config = _acceptance_config(fake_device, model, _RESULTS_MARK)
    config["configurable"]["runtime_goal_context"] = runtime_goal

    result = acceptance_node(base_state, config)

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    per_criterion = result["finish_validation_evidence"]["evidence"]["per_criterion"]
    assert per_criterion["date"]["status"] == "satisfied"
    assert per_criterion["date"]["reason"] == "sealed_by_stage"
    assert per_criterion["date"]["seal"]["stage_id"] == "S1"
    assert "date" in result["finish_validation_evidence"]["matched_terminal_evidence"]
    assert "flight_results" in result["finish_validation_evidence"][
        "matched_terminal_evidence"
    ]


# ----------------------------------------------------------------------
# §7: structured rejection feedback carries stage ids
# ----------------------------------------------------------------------


def test_acceptance_unknown_rejection_feedback_carries_stage_id(
    base_state, fake_device
) -> None:
    contract = _flight_contract()
    reference, runtime_goal = _bind_contract(base_state, contract)
    _seed_state(base_state, contract)
    model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {"criterion": "date", "status": "unknown"},
                        {"criterion": "flight_results", "status": "unknown"},
                    ],
                    "message": "未观察到",
                }
            ),
        )
    )

    config = _acceptance_config(
        fake_device,
        model,
        [
            {
                "mark_id": "home",
                "bbox": [0, 0, 1000, 100],
                "role": "TextView",
                "text_summary": "首页",
            }
        ],
    )
    config["configurable"]["runtime_goal_context"] = runtime_goal
    result = acceptance_node(base_state, config)

    assert result["finished"] is False
    assert result["failure_cause"] == "goal_not_satisfied"
    feedback = result["acceptance_rejection_feedback"]
    assert isinstance(feedback, dict) and isinstance(feedback.get("missing"), list)
    by_criterion = {item["criterion"]: item for item in feedback["missing"]}
    assert set(by_criterion) == {"date", "flight_results"}
    # Stage-owned criterion → stage id + stage hint.
    assert by_criterion["date"]["stage_id"] == "S1"
    assert "S1" in by_criterion["date"]["hint"]
    # Terminal criterion → no stage id + terminal hint.
    assert by_criterion["flight_results"]["stage_id"] is None
    assert "终局" in by_criterion["flight_results"]["hint"]


# ----------------------------------------------------------------------
# §3/§5: the L3 judge receives the bounded ledger digest
# ----------------------------------------------------------------------


def test_judge_receives_ledger_digest_context(base_state, fake_device) -> None:
    """The judge prompt carries the L1/L2 ledger digest AND the trajectory
    summary (S3), so a literal that no longer appears on the final screen is
    still available as mechanical fact and causality is attributable."""
    contract = _flight_contract()
    reference, runtime_goal = _bind_contract(base_state, contract)
    # A digest that does NOT close "date" (different literal) — the judge is
    # still consulted, and must receive the digest as context.
    ledger = append_screen_text_digest(
        [],
        contract_id=reference,
        screen_id="filter",
        observation_epoch=4,
        marks=[
            {
                "mark_id": "ax_filter",
                "source": "accessibility_tree",
                "text_summary": "筛选 06:00-12:00",
            }
        ],
        target_app_entered=True,
    )
    _seed_state(base_state, contract)
    base_state["goal_evidence_ledger"] = ledger
    model = _FakeModelClient(
        _FakeModelResponse(
            "",
            json.dumps(
                {
                    "verdicts": [
                        {
                            "criterion": "date",
                            "status": "satisfied",
                            "observed_value": "日历屏显示2026年10月1日",
                            "evidence_step": "final_screen",
                        },
                        {
                            "criterion": "flight_results",
                            "status": "satisfied",
                            "evidence_step": "final_screen",
                        },
                    ],
                    "message": "done",
                }
            ),
        )
    )

    config = _acceptance_config(fake_device, model, _RESULTS_MARK)
    config["configurable"]["runtime_goal_context"] = runtime_goal
    result = acceptance_node(base_state, config)

    assert result["finished"] is True
    prompt_text = _model_text(model)
    assert "证据账本摘要" in prompt_text
    assert "06:00-12:00" in prompt_text
    # S3: the trajectory summary block is delivered.
    assert "轨迹摘要" in prompt_text
    # Verbatim whitelist delivered (W1-A preserved).
    assert "flight_results" in prompt_text
    assert "date" in prompt_text


# ----------------------------------------------------------------------
# §11: mandatory end-to-end regression — calendar-screen year, final screen
# without the year, finish passes via L1
# ----------------------------------------------------------------------


def test_e2e_calendar_screen_year_observed_then_finish_passes(base_state, fake_device) -> None:
    """The §11 regression (S2 semantics): "2026年10月1日" is read by the
    model on the calendar screen (model_observation, trajectory property);
    the final results screen no longer shows it; the finish claim still
    passes because completion is a trajectory property, not a last-frame
    property."""
    from phone_agent.graph.goal_evidence import append_model_observations

    contract = _flight_contract()
    reference, runtime_goal = _bind_contract(base_state, contract)
    _seed_state(base_state, contract)
    base_state["goal_evidence_ledger"] = append_model_observations(
        [],
        contract_id=reference,
        observations=[
            {
                "criterion": "date",
                "status": "observed",
                "observed_value": "2026年10月1日",
            }
        ],
        step=3,
        screen_id="calendar",
        observation_epoch=3,
    )
    model = _FakeModelClient(
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
                }
            ),
        )
    )

    config = _acceptance_config(fake_device, model, _RESULTS_MARK)
    config["configurable"]["runtime_goal_context"] = runtime_goal
    result = acceptance_node(base_state, config)

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    per_criterion = result["finish_validation_evidence"]["evidence"]["per_criterion"]
    assert per_criterion["date"]["status"] == "satisfied"
    # The reflect read sealed stage S1; the seal is the authoritative reason.
    assert per_criterion["date"]["reason"] in {"sealed_by_stage", "model_observed"}
    assert "date" in result["finish_validation_evidence"]["matched_terminal_evidence"]

    # Negative control: without the recorded read the same final screen cannot
    # pass — the year is simply not on it (fail-closed, absence ≠ success).
    base_state["goal_evidence_ledger"] = []
    rejected = acceptance_node(base_state, config)
    assert rejected["finished"] is False
    assert rejected["failure_cause"] == "goal_not_satisfied"


def test_e2e_reflect_records_calendar_year_then_acceptance_passes(
    base_state, fake_device
) -> None:
    """Full-loop §11 variant: the calendar-screen reflect step itself reads
    the year (model criteria_observations → model_observation ledger entry),
    and the acceptance step on a year-free final screen still passes."""
    from phone_agent.graph.nodes.reflect import reflect_node

    contract = _flight_contract()
    reference, runtime_goal = _bind_contract(base_state, contract)
    base_state["task"] = FLIGHT_TASK

    # Step 1: reflect on the calendar screen where the year is visible; the
    # model reads it as a screen observation.
    base_state["action_parsed"] = {
        "_metadata": "do",
        "action": "Tap",
        "element": [500, 500],
    }
    base_state["expected_outcome"] = {
        "kind": "generic",
        "must_observe": [],
        "must_not_observe": [],
        "target_mark_id": None,
        "target_text_hint": None,
        "timeout_hint": None,
        "dynamic_regions": [],
    }
    base_state["action_result"] = {"success": True, "should_finish": False, "message": "ok"}
    reflect_model = _FakeModelClient(
        _FakeModelResponse(
            "",
            '{"verdict":"succeeded","failure_cause":"none",'
            '"suggested_strategy":"finish","message":"ok",'
            '"criteria_observations":[{"criterion":"date","status":"observed",'
            '"observed_value":"2026年10月1日"}]}',
        )
    )
    reflect_config = {
        "configurable": {
            "model_client": reflect_model,
            "device_factory": fake_device,
            "verbose": False,
            "after_screen_marks": [
                {
                    "mark_id": "ax_cal",
                    "bbox": [50, 200, 900, 300],
                    "role": "TextView",
                    "source": "accessibility_tree",
                    "text_summary": "2026年10月1日",
                }
            ],
            "grounding_provider_name": "off",
            "runtime_goal_context": runtime_goal,
        }
    }
    reflected = reflect_node(base_state, reflect_config)
    ledger = reflected["goal_evidence_ledger"]
    observations = [
        e
        for e in ledger
        if e.get("kind") == "model_observation"
        and e.get("criterion") == "date"
    ]
    assert observations, "reflect step must record the date screen-read"
    assert observations[-1]["status"] == "observed"
    assert "2026年10月1日" in (observations[-1].get("observed_value") or "")

    # Step 2: acceptance on a final screen that no longer shows the year.
    base_state.update(reflected)
    _seed_state(base_state, contract)
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
                }
            ),
        )
    )
    config = _acceptance_config(fake_device, judge_model, _RESULTS_MARK)
    config["configurable"]["runtime_goal_context"] = runtime_goal
    result = acceptance_node(base_state, config)

    assert result["finished"] is True
    assert result["finish_validation_status"] == "success"
    per_criterion = result["finish_validation_evidence"]["evidence"]["per_criterion"]
    assert per_criterion["date"]["status"] == "satisfied"
    # The reflect read sealed stage S1; the seal is the authoritative reason.
    assert per_criterion["date"]["reason"] in {"sealed_by_stage", "model_observed"}
    assert "date" in result["finish_validation_evidence"]["matched_terminal_evidence"]


# ----------------------------------------------------------------------
# §4.1: compile-time terminal-literal warning
# ----------------------------------------------------------------------


def test_terminal_literal_warning_unit() -> None:
    contract = _flight_contract()
    # The date literal is staged → no warning; the terminal criterion's
    # description carries no literal → no warning.
    assert terminal_literal_warnings(contract) == []

    terminal_date = GoalContract(
        task_hash="w1",
        redacted_objective="订票",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "date",
                "出发日期 2026年10月1日",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "2026年10月1日"
                ),
            )
        ],
        compile_status="compiled",
    )
    assert terminal_literal_warnings(terminal_date) == [
        {"criterion": "date", "literal_kinds": ["full_date_literal"]}
    ]

    terminal_interval = GoalContract(
        task_hash="w2",
        redacted_objective="订票",
        objective_length=4,
        success_criteria=[
            SuccessCriterion(
                "time",
                "出发时段 06:00-12:00",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "06:00-12:00"
                ),
            )
        ],
        compile_status="compiled",
    )
    kinds = terminal_literal_warnings(terminal_interval)
    assert len(kinds) == 1 and kinds[0]["literal_kinds"] == ["interval_literal"]


def test_goal_node_emits_terminal_literal_warning() -> None:
    """The warning is trace-visible (goal_node, non-blocking)."""
    from phone_agent.graph.goal_requirements import TaskRequirementExtractor
    from phone_agent.graph.runtime_goal import RuntimeGoalContext

    task = "订 2026年10月1日 06:00-12:00 的机票"
    requirements = TaskRequirementExtractor().extract(task)
    contract = GoalContract(
        task_hash=requirements.task_hash,
        redacted_objective=task,
        objective_length=len(task),
        success_criteria=[
            SuccessCriterion(
                "date",
                "出发日期 2026年10月1日",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "2026年10月1日"
                ),
                required=True,
            ),
            # L1: every explicit parameter constraint (here the interval) needs
            # its own covered criterion — the adequacy gate now rejects
            # contracts that carry only the date.
            SuccessCriterion(
                "time_window",
                "筛选面板显示 '06:00-12:00' 时段",
                "vlm_judge",
                predicate=CORE_PREDICATE_CATALOG.create_spec(
                    "semantic.entity_matches", "06:00-12:00"
                ),
                provenance="confirmed",
                required=True,
            ),
        ],
        target_app_hint="flights",
        entities_sha=list(requirements.target_entity_hashes),
        compile_status="compiled",
        compile_source="external",
    )
    writer = _FakeTraceWriter()
    result = goal_node(
        {
            "task": task,
            "lang": "cn",
            "step_count": 0,
            "goal_contract_status": "pending",
        },
        {
            "configurable": {
                "runtime_goal_context": RuntimeGoalContext(),
                "task_goal_contract_override": contract,
                "task_requirement_set_override": requirements,
                "trace_writer": writer,
            }
        },
    )
    assert result["goal_contract_status"] == "user_override"
    warnings = [
        event["payload"]
        for event in writer.events
        if event["event"] == "terminal_literal_warning"
    ]
    by_criterion = {item["criterion"]: item["literal_kinds"] for item in warnings}
    assert by_criterion["date"] == ["full_date_literal"]
    assert by_criterion["time_window"] == ["interval_literal"]
