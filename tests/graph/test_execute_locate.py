"""F1 locate tool: execute-node dispatch branches + full locate→tap chain."""

import pytest

from phone_agent.actions.grounding import ground_intent_to_action
from phone_agent.config.policy import (
    LOCATE_MAX_MARKS_PER_SCREEN,
    LOCATE_MAX_PER_RUN,
)
from phone_agent.graph.context import (
    build_plan_context_block,
    locate_hint_digest,
)
from phone_agent.graph.edges import after_execute
from phone_agent.graph.marks import Mark, MarkRegistry, compute_raw_screenshot_hash
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.grounding.fake import FakeGroundingProvider

_SCREEN = "screen-1"
_RAW = "fake-image"
_RAW_HASH = compute_raw_screenshot_hash(_RAW)
_SURFACE = "com.example/.MainActivity"


def _observation(surface: str = _SURFACE) -> dict:
    return {"snapshot": {"foreground_activity": surface}}


def _mark_registry(locate_marks: int = 0) -> MarkRegistry:
    marks = {
        "m1": Mark(
            mark_id="m1",
            screen_id=_SCREEN,
            bbox=(0, 0, 100, 100),
            center=(50, 50),
            source="accessibility",
            role="TextView",
            text_summary="首页",
        )
    }
    for index in range(1, locate_marks + 1):
        marks[f"locate_{index}"] = Mark(
            mark_id=f"locate_{index}",
            screen_id=_SCREEN,
            bbox=(0, 0, 10, 10),
            center=(5, 5),
            source="locateanything_mlx",
            confidence=1.0,
        )
    registry = MarkRegistry(
        screen_id=_SCREEN,
        marks=marks,
        semantic_screen_id="semantic-1",
        observation_epoch=1,
        raw_screenshot_hash=_RAW_HASH,
    )
    return registry


def _state_with_locate(base_state, **overrides) -> dict:
    state = dict(base_state)
    state["action_parsed"] = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月1日",
    }
    state["action_raw"] = (
        '{"type":"intent","action":"locate","target_text_hint":"10月1日"}'
    )
    state["mark_registry"] = _mark_registry().to_dict()
    state["locate_count"] = 0
    state.update(overrides)
    return state


def _config(provider, fake_device) -> dict:
    return {
        "configurable": {
            "device_factory": fake_device,
            "verbose": False,
            "locate_provider": provider,
        }
    }


# ----------------------------------------------------------------------
# Execute three branches (mock LA provider)
# ----------------------------------------------------------------------


def test_execute_locate_one_box_registers_mark_and_routes_replan(
    base_state, fake_device
) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _state_with_locate(base_state), _config(provider, fake_device)
    )

    assert result["finished"] is False
    assert result["action_result"]["success"] is True
    assert result["action_receipt"]["dispatch_status"] == "accepted"
    assert result["action_receipt"]["side_effect_receipt"]["mark_id"] == "locate_1"
    assert result["locate_count"] == 1
    registry = MarkRegistry.from_dict(result["mark_registry"])
    assert registry is not None
    assert registry.screen_id == _SCREEN
    assert "locate_1" in registry.marks
    locate_mark = registry.marks["locate_1"]
    assert locate_mark.bbox == (400.0, 400.0, 600.0, 600.0)
    assert locate_mark.role is None
    assert locate_mark.source == "fake"
    assert locate_mark.confidence == 1.0
    # No device interaction at all (only the screenshot re-capture).
    assert fake_device.calls == [("get_screenshot", ("device-1",), {})]
    # after_execute routes via the capability (requires_reobservation=False).
    routed_state = {
        **base_state,
        **result,
        "action_parsed": {
            "_metadata": "do",
            "action": "Locate",
            "target_text_hint": "10月1日",
        },
    }
    assert after_execute(routed_state) == "replan"


def test_execute_locate_no_candidate_fails_closed(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(failure_code="grounding_no_candidate")
    result = execute_node(
        _state_with_locate(base_state), _config(provider, fake_device)
    )

    assert result["finished"] is False
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "grounding_no_candidate"
    assert result["grounding_failure_code"] == "grounding_no_candidate"
    assert result["action_receipt"]["dispatch_status"] == "rejected"
    assert "locate_1" not in (result.get("mark_registry") or {}).get("marks", {})
    # H2: failed attempts consume the per-run locate budget too.
    assert result["locate_count"] == 1
    assert fake_device.calls == [("get_screenshot", ("device-1",), {})]


def test_execute_locate_multiple_boxes_ambiguous_fails_closed(
    base_state, fake_device
) -> None:
    provider = FakeGroundingProvider(
        bboxes=[[100, 100, 300, 300], [500, 500, 700, 700]]
    )
    result = execute_node(
        _state_with_locate(base_state), _config(provider, fake_device)
    )

    assert result["finished"] is False
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "grounding_ambiguous"
    assert "locate_1" not in (result.get("mark_registry") or {}).get("marks", {})
    assert fake_device.calls == [("get_screenshot", ("device-1",), {})]


def test_execute_locate_budget_exhausted_rejects_without_provider_call(
    base_state, fake_device
) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(
        _state_with_locate(base_state, locate_count=LOCATE_MAX_PER_RUN),
        _config(provider, fake_device),
    )

    assert result["finished"] is False
    assert result["failure_cause"] == "locate_budget_exhausted"
    assert result["action_result"]["success"] is False
    assert result["action_receipt"]["side_effect_receipt"]["reason_code"] == "locate_budget_exhausted"
    assert provider.requests == []
    assert fake_device.calls == []


def test_execute_locate_per_screen_mark_limit_rejects(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _state_with_locate(base_state)
    state["mark_registry"] = _mark_registry(
        locate_marks=LOCATE_MAX_MARKS_PER_SCREEN
    ).to_dict()
    result = execute_node(state, _config(provider, fake_device))

    assert result["finished"] is False
    assert result["failure_cause"] == "locate_screen_mark_limit"
    assert provider.requests == []


def test_execute_locate_drifted_screen_registers_mark_atomically(
    base_state, fake_device
) -> None:
    """H1: a drifted frame (F differs from the registry observation) is NOT
    rejected. The query runs against F, the mark merges onto the same screen_id
    (with_extra_marks semantics) and the registry is rebound to hash_F."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _state_with_locate(base_state)
    registry = _mark_registry()
    registry = MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id=registry.semantic_screen_id,
        observation_epoch=registry.observation_epoch,
        mark_set_version=registry.mark_set_version,
        perceptual_hash=registry.perceptual_hash,
        raw_screenshot_hash=compute_raw_screenshot_hash("old-image"),
    )
    state["mark_registry"] = registry.to_dict()
    result = execute_node(state, _config(provider, fake_device))

    assert result["finished"] is False
    assert result["action_result"]["success"] is True
    assert result["locate_count"] == 1
    # Drift is recorded, never a rejection.
    assert result["observation_drifted"] is True
    new_registry = MarkRegistry.from_dict(result["mark_registry"])
    assert new_registry is not None
    # with_extra_marks semantics: same screen_id, mark merged, version recomputed.
    assert new_registry.screen_id == _SCREEN
    assert "locate_1" in new_registry.marks
    assert new_registry.mark_set_version != registry.mark_set_version
    # The mark is bound to the frame LA actually saw (hash_F), not the old one.
    assert new_registry.raw_screenshot_hash == _RAW_HASH
    assert new_registry.raw_screenshot_hash != registry.raw_screenshot_hash
    # LA received the F-bound binding.
    assert provider.requests[0]["screen_binding"]["raw_screenshot_hash"] == _RAW_HASH
    assert provider.requests[0]["screen_binding"]["screen_id"] == _SCREEN


def test_execute_locate_same_frame_stays_bound_and_not_drifted(
    base_state, fake_device
) -> None:
    """H1: when F matches the registry snapshot there is no drift marker and the
    registry hash is unchanged (rebind is a no-op)."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    result = execute_node(_state_with_locate(base_state), _config(provider, fake_device))

    assert result["action_result"]["success"] is True
    assert result["observation_drifted"] is False
    new_registry = MarkRegistry.from_dict(result["mark_registry"])
    assert new_registry is not None
    assert new_registry.raw_screenshot_hash == _RAW_HASH


def test_locate_outcome_reports_drift_for_diagnostics(base_state) -> None:
    """H1: locate_target itself reports observation_drifted for the drifted
    frame and keeps it False for a matching frame."""
    from phone_agent.graph.tools.locate import locate_target

    class _Device:
        def __init__(self, payload: str) -> None:
            self.payload = payload

        def get_screenshot(self, device_id=None):
            return type("Shot", (), {"base64_data": self.payload, "width": 1000, "height": 2000})()

    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    drifted_state = _state_with_locate(base_state)
    registry = _mark_registry()
    registry = MarkRegistry(
        screen_id=registry.screen_id,
        marks=registry.marks,
        semantic_screen_id=registry.semantic_screen_id,
        observation_epoch=registry.observation_epoch,
        mark_set_version=registry.mark_set_version,
        perceptual_hash=registry.perceptual_hash,
        raw_screenshot_hash=compute_raw_screenshot_hash("old-image"),
    )
    drifted_state["mark_registry"] = registry.to_dict()
    drifted = locate_target(
        drifted_state,
        {"configurable": {"device_factory": _Device("fake-image"), "locate_provider": provider}},
    )
    assert drifted.success is True
    assert drifted.observation_drifted is True
    assert drifted.raw_screenshot_hash == _RAW_HASH

    same_state = _state_with_locate(base_state)
    same = locate_target(
        same_state,
        {"configurable": {"device_factory": _Device("fake-image"), "locate_provider": provider}},
    )
    assert same.success is True
    assert same.observation_drifted is False


def test_execute_locate_missing_registry_fails_closed(base_state, fake_device) -> None:
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _state_with_locate(base_state)
    state["mark_registry"] = None
    result = execute_node(state, _config(provider, fake_device))

    assert result["finished"] is False
    assert result["failure_cause"] == "registry_missing"
    assert provider.requests == []


def test_execute_locate_failure_message_reaches_next_plan_context(
    base_state, fake_device
) -> None:
    """replan skips reflect: the failure must still reach the next plan via the
    action_outcome_summary context (F1.2, pit #5)."""
    provider = FakeGroundingProvider(failure_code="grounding_no_candidate")
    result = execute_node(
        _state_with_locate(base_state), _config(provider, fake_device)
    )

    outcome = result["action_outcome_summary"]
    assert outcome["failure_cause"] == "grounding_no_candidate"
    assert outcome["dispatch_status"] == "rejected"


def test_execute_locate_failure_renders_failure_code_in_next_plan_block(
    base_state, fake_device
) -> None:
    """H3: the locate failure branch writes failure_code + attempt count into
    action_outcome_summary so the next plan round's last_action_outcome renders
    the failure reason (locate skips reflect)."""
    provider = FakeGroundingProvider(failure_code="grounding_no_candidate")
    state = _state_with_locate(base_state)
    state["observation"] = _observation()
    state["locate_count"] = 1
    result = execute_node(state, _config(provider, fake_device))

    assert result["action_outcome_summary"]["failure_code"] == "grounding_no_candidate"
    assert result["action_outcome_summary"]["locate_count"] == 2
    # Next round's plan context is the pre-execute state merged with the
    # execute updates (the real graph reducer flow), so the failure code and
    # the attempt count both render in last_action_outcome.
    block, _metrics = build_plan_context_block({**state, **result})
    assert "grounding_no_candidate" in block
    assert "Locate" in block
    assert '"locate_count": 2' in block


def test_execute_locate_repeat_guard_rejects_same_query_on_same_surface(
    base_state, fake_device
) -> None:
    """H4: identical locate query on one surface repeats are rejected by the
    execute repeat guard before any provider call."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _state_with_locate(base_state)
    state["observation"] = _observation()
    digest = locate_hint_digest("10月1日")
    assert digest is not None
    state["gui_memory"] = {
        "tried_actions": [
            {"action": "Locate", "surface": _SURFACE, "hint_digest": digest},
            {"action": "Locate", "surface": _SURFACE, "hint_digest": digest},
        ]
    }
    result = execute_node(state, _config(provider, fake_device))

    assert result["finished"] is False
    assert result["action_result"]["success"] is False
    assert result["failure_cause"] == "repeated_action"
    assert result["repeat_rejected"] is True
    # The guard fires before the Locate branch: no screenshot, no provider call.
    assert fake_device.calls == []
    assert provider.requests == []


def test_execute_locate_different_hint_on_same_surface_is_not_a_repeat(
    base_state, fake_device
) -> None:
    """H4: a different query on the same surface has a different digest, so the
    repeat guard does not fire."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])
    state = _state_with_locate(base_state)
    state["observation"] = _observation()
    other_digest = locate_hint_digest("另一个目标")
    state["gui_memory"] = {
        "tried_actions": [
            {"action": "Locate", "surface": _SURFACE, "hint_digest": other_digest},
            {"action": "Locate", "surface": _SURFACE, "hint_digest": other_digest},
        ]
    }
    result = execute_node(state, _config(provider, fake_device))

    assert result["action_result"]["success"] is True
    assert result["locate_count"] == 1


def test_update_gui_memory_records_locate_with_hint_digest(base_state) -> None:
    """H4: Locate enters tried_actions (the _metadata == "do" gate is opened for
    Locate) carrying a sanitized hint digest, never the raw query text."""
    from phone_agent.graph.context import update_gui_memory

    state = dict(base_state)
    state["observation"] = _observation()
    state["action_parsed"] = {
        "_metadata": "do",
        "action": "Locate",
        "target_text_hint": "10月1日",
    }
    state["action_result"] = {"success": False, "message": "Locate failed: grounding_no_candidate"}
    state["failure_cause"] = "grounding_no_candidate"
    state["step_count"] = 3
    memory = update_gui_memory(
        state,
        current_app="FakeApp",
        screen_id=_SCREEN,
        reached_surface=_SURFACE,
    )

    tried = memory["tried_actions"]
    assert tried and tried[-1]["action"] == "Locate"
    assert tried[-1]["surface"] == _SURFACE
    assert tried[-1]["hint_digest"] == locate_hint_digest("10月1日")
    assert "10月1日" not in str(tried[-1])


def test_locate_repeat_key_is_surface_and_digest_scoped(base_state) -> None:
    """H4: the Locate repeat key is (Locate, surface, hint_digest): same query on
    a different surface is not the same key."""
    from phone_agent.graph.context import repeated_action_key

    digest = locate_hint_digest("10月1日")
    entry = {"action": "Locate", "surface": _SURFACE, "hint_digest": digest}
    other_surface = {"action": "Locate", "surface": "com.other/.Activity", "hint_digest": digest}
    assert repeated_action_key(entry) == ("Locate", _SURFACE, digest)
    assert repeated_action_key(entry) != repeated_action_key(other_surface)


# ----------------------------------------------------------------------
# Integration: locate → merged mark → grounded tap lands on LA coordinates
# ----------------------------------------------------------------------


def test_locate_then_grounded_tap_lands_on_la_coordinates(
    base_state, fake_device
) -> None:
    """Full chain: target not in accessibility marks → model locates → the new
    locate_N mark grounds a tap that executes on the visual provider's box."""
    provider = FakeGroundingProvider(bbox=[400, 400, 600, 600])

    # Step 1: locate registers locate_1 on the current screen.
    locate_result = execute_node(
        _state_with_locate(base_state), _config(provider, fake_device)
    )
    assert locate_result["action_result"]["success"] is True
    assert locate_result["locate_count"] == 1

    state_after_locate = {
        **base_state,
        "mark_registry": locate_result["mark_registry"],
        "locate_count": locate_result["locate_count"],
    }

    # Step 2: the model now emits a tap intent on the registered mark; grounding
    # compiles it to an executable tap at the LA box center (0-1000 relative).
    grounded = ground_intent_to_action(
        {"_metadata": "intent", "action": "Tap", "target_mark_id": "locate_1"},
        mark_registry=MarkRegistry.from_dict(state_after_locate["mark_registry"]),
        screen_id=_SCREEN,
    )
    assert grounded["action"] == "Tap"
    assert grounded["element"] == [500, 500]

    # Step 3: execute dispatches the tap on the device (relative → absolute).
    state_after_locate["action_parsed"] = grounded
    state_after_locate["action_raw"] = '{"type":"intent","action":"tap","target_mark_id":"locate_1"}'
    tap_result = execute_node(
        state_after_locate, {"configurable": {"device_factory": fake_device, "verbose": False}}
    )
    assert tap_result["action_result"]["success"] is True
    # 1000px width → 500 rel = 500 px; 2000px height → 500 rel = 1000 px.
    assert fake_device.calls[-1] == ("tap", (500, 1000, "device-1"), {})
