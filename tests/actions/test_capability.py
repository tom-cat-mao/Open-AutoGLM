from phone_agent.actions.capability import get_all_capabilities
from phone_agent.actions.validator import CANONICAL_ACTIONS


def test_every_canonical_action_has_valid_capability_metadata() -> None:
    capabilities = get_all_capabilities()
    by_name = {capability.action_name: capability for capability in capabilities}

    assert set(by_name) == CANONICAL_ACTIONS
    assert len(by_name) == len(capabilities)
    for capability in capabilities:
        assert capability.capability_id.startswith("phone_agent.action.")
        assert capability.version == "capability_gate_v1"
        assert capability.implementation_status in {
            "implemented",
            "unavailable",
            "delegated",
        }
        if capability.side_effect_kind in {"device_local", "external"}:
            assert capability.required_postconditions


def test_wait_and_stub_capability_semantics_are_explicit() -> None:
    by_name = {
        capability.action_name: capability
        for capability in get_all_capabilities()
    }

    assert by_name["Wait"].requires_reobservation is True
    assert by_name["Note"].implementation_status == "unavailable"
    assert by_name["Call_API"].implementation_status == "unavailable"
    assert by_name["Interact"].implementation_status == "delegated"
    assert by_name["Interact"].hitl_policy_id == "takeover"
