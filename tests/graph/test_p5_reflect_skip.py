"""P5 speedup: deterministic reflect skip on high verifier confidence.

Covers:
- verifier success (confidence >= 0.9) with no pending vlm_judge criterion
  skips the reflect model call and produces a deterministic ReflectionResult
- pending vlm_judge criteria force the model call (evidence still needs
  collecting)
- the config switch turns the skip off (old path restored)
- same-page selected_object_match (0.75 confidence, P3-gated) also skips,
  while a cross-page skipped check (page_changed_object_check_skipped) never
  produces a false skip signal
- trace reflect_result carries model_skipped=True + code-only skip reason
"""

import json
from dataclasses import dataclass

from phone_agent.graph.goal import CriterionSpec, GoalContract
from phone_agent.graph.nodes.reflect import _reflection_from_verifier, reflect_node
from phone_agent.graph.trace import JsonlTraceWriter
from phone_agent.graph.verifier import VerifierResult


@dataclass
class FakeModelResponse:
    thinking: str
    action: str
    parse_metadata: dict | None = None


class CountingModelClient:
    """Model client that counts calls and returns a canned failure verdict."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages = None

    def request(self, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        return FakeModelResponse(
            "ok",
            '{"verdict":"failed","failure_cause":"unknown",'
            '"suggested_strategy":"retry","message":"not sure"}',
        )


def _programmatic_contract(task: str = "打开设置页") -> GoalContract:
    """A contract with no vlm_judge criteria: nothing awaits model evidence."""

    return GoalContract(
        task_hash="p5-test",
        redacted_objective=task,
        objective_length=len(task),
        compile_status="compiled",
        success_criteria=[
            CriterionSpec(
                name="settings_open",
                description="设置页已打开",
                verification="app_or_activity_match",
                required=True,
            )
        ],
    )


def _launch_state(base_state, contract: GoalContract) -> dict:
    state = dict(base_state)
    state.update(
        {
            "action_parsed": {
                "_metadata": "do",
                "action": "Launch",
                "app": "FakeApp",
            },
            "expected_outcome": {
                "kind": "app_opened",
                "must_observe": [],
                "must_not_observe": [],
                "target_mark_id": None,
                "target_text_hint": None,
                "timeout_hint": None,
                "dynamic_regions": [],
            },
            "goal_contract": contract,
            "goal_contract_status": "compiled",
        }
    )
    return state


def _reflect_config(model, device, **overrides) -> dict:
    config: dict = {
        "model_client": model,
        "device_factory": device,
        "verbose": False,
        "grounding_provider_name": "off",
    }
    config.update(overrides)
    return {"configurable": config}


# ---------------------------------------------------------------------------
# Unit-level skip judgment
# ---------------------------------------------------------------------------


def test_high_confidence_success_skips_model_call() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.9,
            evidence={"matched_postconditions": ["surface_changed"]},
        ),
        action={"action": "Swipe"},
        liveness={"state": "advancing"},
        goal_agenda=[],
    )

    assert result is not None
    assert result.verdict == "succeeded"
    assert result.suggested_strategy == "continue"
    assert result.model_skipped is True
    assert "surface_changed" in (result.model_skip_reason or "")


def test_low_confidence_success_does_not_skip() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.4,
            evidence={"matched_postconditions": []},
        ),
        action={"action": "Tap"},
        liveness={"state": "advancing"},
        goal_agenda=[],
    )

    assert result is None


def test_pending_vlm_judge_blocks_skip() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.95,
            evidence={"matched_postconditions": ["app_opened"]},
        ),
        action={"action": "Launch"},
        liveness={"state": "advancing"},
        goal_agenda=[
            {
                "verification": "vlm_judge",
                "status": "missing",
            },
            {
                "verification": "app_or_activity_match",
                "status": "satisfied",
            },
        ],
    )

    assert result is None


def test_latched_vlm_judge_allows_skip() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.95,
            evidence={"matched_postconditions": ["app_opened"]},
        ),
        action={"action": "Launch"},
        liveness={"state": "advancing"},
        goal_agenda=[
            {
                "verification": "vlm_judge",
                "status": "satisfied",
                "latched": True,
            }
        ],
    )

    assert result is not None
    assert result.model_skipped is True


def test_stuck_trajectory_blocks_skip() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.95,
            evidence={"matched_postconditions": ["app_opened"]},
        ),
        action={"action": "Launch"},
        liveness={"state": "stuck"},
        goal_agenda=[],
    )

    assert result is None


def test_selected_object_match_skips_but_skip_reason_is_code_only() -> None:
    """selected_object_match (0.75 confidence) skips only on same-page
    evidence; the reason embeds machine codes, never raw on-screen text."""

    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.75,
            evidence={
                "matched_postconditions": ["selected_object_match"],
                "selected_object_signals": {
                    "selected_object_text_match": True,
                    "page_changed_object_check_skipped": False,
                },
            },
        ),
        action={"action": "Tap"},
        liveness={"state": "advancing"},
        goal_agenda=[],
    )

    assert result is not None
    assert result.model_skipped is True
    assert "selected_object_match" in (result.model_skip_reason or "")
    assert "13800138000" not in (result.model_skip_reason or "")


def test_cross_page_selected_object_signal_never_skips() -> None:
    """P3 #4 degradation: a cross-page rebuild suppresses the whole
    selected-object group, so the skip branch cannot be triggered by a stale
    page-bound signal."""

    result = _reflection_from_verifier(
        VerifierResult(
            status="unknown",
            confidence=0.0,
            evidence={
                "matched_postconditions": [],
                "selected_object_signals": {
                    "page_changed_object_check_skipped": True
                },
            },
        ),
        action={"action": "Tap"},
        liveness={"state": "advancing"},
        goal_agenda=[],
    )

    assert result is None


def test_hard_failure_still_deterministic_and_flags_skip() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="failure",
            confidence=0.9,
            hard_failure=True,
            failure_cause="app_not_responding",
        ),
        action={"action": "Tap"},
        liveness={"state": "advancing"},
        goal_agenda=[],
    )

    assert result is not None
    assert result.verdict == "failed"
    assert result.suggested_strategy == "retry"
    assert result.model_skipped is True
    assert result.model_skip_reason == "hard_failure"


def test_disabled_switch_restores_model_path() -> None:
    result = _reflection_from_verifier(
        VerifierResult(
            status="success",
            confidence=0.95,
            evidence={"matched_postconditions": ["app_opened"]},
        ),
        action={"action": "Launch"},
        liveness={"state": "advancing"},
        goal_agenda=[],
        allow_skip=False,
    )

    assert result is None


# ---------------------------------------------------------------------------
# reflect_node integration: model call accounting + trace
# ---------------------------------------------------------------------------


def test_reflect_node_skips_model_on_high_confidence_no_vlm_pending(
    base_state, fake_device, tmp_path
) -> None:
    writer = JsonlTraceWriter(trace_id="p5-skip", trace_dir=tmp_path, redact=True)
    model = CountingModelClient()
    state = _launch_state(base_state, _programmatic_contract())

    result = reflect_node(state, _reflect_config(model, fake_device, trace_writer=writer))

    assert model.calls == 0
    assert result["model_skipped"] is True
    assert result["reflection_verdict"] == "succeeded"
    assert result["action_succeeded"] is True
    assert result["verifier_status"] == "success"

    records = [
        json.loads(line)
        for line in writer.path.read_text(encoding="utf-8").splitlines()
    ]
    reflect_result = next(item for item in records if item["event"] == "reflect_result")
    assert reflect_result["payload"]["model_skipped"] is True
    assert "verifier_high_confidence" in reflect_result["payload"]["model_skip_reason"]


def test_reflect_node_pending_vlm_judge_still_calls_model(
    base_state, fake_device
) -> None:
    # base_state's heuristic contract is all-vlm_judge: the skip precondition
    # fails, so the model must still be called.
    model = CountingModelClient()
    state = _launch_state(base_state, base_state["goal_contract"])

    result = reflect_node(state, _reflect_config(model, fake_device))

    assert model.calls == 1
    assert result["model_skipped"] is False
    # verifier success vs model failed → P3 disputed (partial), not a clean
    # failure — the skip precondition correctly kept the model in the loop.
    assert result["reflection_verdict"] == "partial"


def test_reflect_node_switch_off_restores_model_path(base_state, fake_device) -> None:
    model = CountingModelClient()
    state = _launch_state(base_state, _programmatic_contract())

    result = reflect_node(
        state,
        _reflect_config(
            model, fake_device, skip_reflect_on_high_confidence=False
        ),
    )

    assert model.calls == 1
    assert result["model_skipped"] is False
    # same arbitration as the pending-vlm_judge case: verifier success vs
    # model failed → disputed partial.
    assert result["reflection_verdict"] == "partial"


def test_reflect_node_typed_text_success_skips_with_code_only_reason(
    base_state, fake_device, tmp_path
) -> None:
    writer = JsonlTraceWriter(trace_id="p5-typed", trace_dir=tmp_path, redact=True)
    model = CountingModelClient()
    state = dict(base_state)
    state.update(
        {
            "action_parsed": {"_metadata": "do", "action": "Type", "text": "村长托马斯"},
            "expected_outcome": {
                "kind": "text_present",
                "must_observe": ["村长托马斯"],
                "must_not_observe": [],
                "target_mark_id": None,
                "target_text_hint": None,
                "timeout_hint": None,
                "dynamic_regions": [],
            },
            "goal_contract": _programmatic_contract(),
            "goal_contract_status": "compiled",
        }
    )

    result = reflect_node(
        state,
        _reflect_config(
            model,
            fake_device,
            trace_writer=writer,
            after_screen_marks=[
                {
                    "mark_id": "ax_1",
                    "bbox": [0, 0, 100, 100],
                    "role": "TextView",
                    "text_summary": "村长托马斯",
                }
            ],
        ),
    )

    assert model.calls == 0
    assert result["model_skipped"] is True
    assert result["reflection_verdict"] == "succeeded"
    assert result["verifier_status"] == "success"

    records = [
        json.loads(line)
        for line in writer.path.read_text(encoding="utf-8").splitlines()
    ]
    reflect_result = next(item for item in records if item["event"] == "reflect_result")
    skip_reason = reflect_result["payload"]["model_skip_reason"]
    assert "typed_text_present" in skip_reason
    # The reason and reflection carry machine codes only, never raw screen text.
    assert "村长托马斯" not in skip_reason
    assert "村长托马斯" not in (result["reflection"] or "")
