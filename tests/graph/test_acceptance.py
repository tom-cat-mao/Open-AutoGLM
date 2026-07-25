"""Acceptance node: terminal goal verification, split out of Reflect."""

from phone_agent.graph.edges import after_acceptance, after_execute
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
