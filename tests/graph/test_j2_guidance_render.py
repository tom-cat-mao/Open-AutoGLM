from __future__ import annotations

from phone_agent.graph.context import build_plan_context_block, default_context_budget


def _goal_agenda(*names: str) -> list[dict[str, object]]:
    return [
        {
            "description": name,
            "status": "unknown",
            "verification": "vlm_judge",
            "predicate_id": None,
        }
        for name in names
    ]


def test_j2_last_action_outcome_renders_parse_failure_and_layer_policy() -> None:
    state = {
        "action_parsed": {"action": "Tap"},
        "action_result": {"success": False, "message": "validation failed"},
        "error_layer": "validation",
        "retry_policy": "parse_retry",
        "parse_failure": {
            "code": "missing_field",
            "layer": "validation",
            "expected": {"field": "text", "type": "string"},
            "found": {"field": "element[1]", "value": 1234},
        },
    }

    block, _metrics = build_plan_context_block(state, lang="en")

    assert "last_action_outcome:" in block
    assert '"error_layer": "validation"' in block
    assert '"retry_policy": "parse_retry"' in block
    assert (
        'parse_failure: layer=validation code=missing_field '
        'expected={"field":"text","type":"string"} '
        'found={"field":"element[1]","value":1234}'
    ) in block


def test_j2_parse_failure_found_private_key_degrades_before_render() -> None:
    state = {
        "parse_failure": {
            "code": "bad_field",
            "layer": "validation",
            "expected": {"field": "mark_id", "type": "string"},
            "found": {"message": "secret answer 13800138000", "field": "text"},
        },
    }

    block, _metrics = build_plan_context_block(state, lang="en")

    assert "parse_failure: layer=validation code=bad_field" in block
    assert 'found={"redacted":true,"length":' in block
    assert "secret answer" not in block
    assert "13800138000" not in block


def test_j2_system_guidance_renders_as_mechanism_level_advisory() -> None:
    state = {
        "mechanism_suggestion": "Use the validation repair path before dispatch.",
    }

    block, _metrics = build_plan_context_block(state, lang="en")

    assert (
        "[system_guidance] (mechanism-level hint, advisory only): "
        "Use the validation repair path before dispatch."
    ) in block
    assert "system_guidance" in block
    assert "suggested_strategy" not in block


def test_j2_system_guidance_has_independent_budget_and_trims_tail() -> None:
    state = {
        "mechanism_suggestion": "mechanism hint " * 30,
    }

    block, metrics = build_plan_context_block(state, lang="en")
    guidance_line = next(line for line in block.splitlines() if "system_guidance" in line)

    assert len(guidance_line) <= 160
    assert guidance_line.endswith("...<truncated>")
    assert metrics["context_truncated"] is True


def test_j2_acceptance_rejection_renders_verdicts_and_rejection_judge() -> None:
    state = {
        "goal_agenda": _goal_agenda("criterion_a"),
        "acceptance_rejection_feedback": {
            "missing": [
                {
                    "criterion": "criterion_a",
                    "stage_id": "terminal",
                    "hint": "collect bounded evidence",
                }
            ]
        },
        "acceptance_verdicts": {
            "criterion_a": {"status": "unknown", "reason": "missing_evidence"},
            "criterion_b": {"status": "contradicted", "reason": "positive_counter"},
        },
        "finish_validation_status": "unknown",
        "reflection": "judge says criterion_a lacks a grounded evidence step " * 4,
    }

    block, _metrics = build_plan_context_block(state, lang="en")

    assert "acceptance_rejection:" in block
    assert "criterion_a [terminal]: collect bounded evidence" in block
    assert "verdict: criterion_a status=unknown reason=missing_evidence" in block
    assert "criterion_b" not in block
    assert "judge: judge says criterion_a lacks a grounded evidence step" in block
    judge_line = next(line for line in block.splitlines() if line.startswith("judge:"))
    assert len(judge_line.removeprefix("judge: ")) <= 100


def test_j2_empty_guidance_fields_and_non_rejection_skip_empty_sections() -> None:
    state = {
        "acceptance_verdicts": {},
        "mechanism_suggestion": None,
        "finish_validation_status": "success",
        "reflection": "this judge text must not render",
        "action_parsed": {"action": "Wait"},
        "action_result": {"success": True, "message": "ok"},
    }

    block, _metrics = build_plan_context_block(state, lang="en")

    assert "system_guidance" not in block
    assert "acceptance_rejection:" not in block
    assert "verdict:" not in block
    assert "judge:" not in block


def test_j2_budget_trimming_keeps_agenda_when_system_guidance_is_tail() -> None:
    budget = default_context_budget()
    budget["context_block_chars"] = 260
    budget["goal_agenda_chars"] = 220
    state = {
        "goal_agenda": _goal_agenda("agenda must survive"),
        "failure_memory": [
            {
                "step_count": index,
                "action": "Tap",
                "failure_cause": "element_not_found",
                "suggested_strategy": "retry with a different mechanism",
            }
            for index in range(8)
        ],
        "mechanism_suggestion": "tail guidance " * 20,
        "context_budget": budget,
    }

    block, metrics = build_plan_context_block(state, lang="en")

    assert "goal_agenda" in block
    assert "agenda must survive" in block
    assert metrics["context_truncated"] is True
