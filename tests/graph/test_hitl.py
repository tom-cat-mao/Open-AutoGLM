from phone_agent.graph.nodes import confirm as confirm_module
from phone_agent.graph.nodes import takeover as takeover_module


def test_confirm_accept_keeps_pending_execute(base_state, monkeypatch) -> None:
    monkeypatch.setattr(confirm_module, "interrupt", lambda payload: "Y")
    base_state["interrupt_message"] = "支付确认"
    base_state["pending_execute"] = True

    result = confirm_module.confirm_node(base_state, {})

    assert result["interrupt_result"] is True
    assert result["pending_execute"] is True
    assert result["finished"] is False


def test_confirm_reject_finishes_and_records_cancel(base_state, monkeypatch) -> None:
    monkeypatch.setattr(confirm_module, "interrupt", lambda payload: False)
    base_state["pending_execute"] = True

    result = confirm_module.confirm_node(base_state, {})

    assert result["interrupt_result"] is False
    assert result["finished"] is True
    assert result["action_result"]["should_finish"] is True


def test_takeover_clears_interrupt_state(base_state, monkeypatch) -> None:
    payloads = []
    monkeypatch.setattr(
        takeover_module, "interrupt", lambda payload: payloads.append(payload)
    )
    base_state["interrupt_message"] = "请登录"

    result = takeover_module.takeover_node(base_state, {})

    assert payloads[0]["type"] == "takeover"
    assert result == {"pending_interrupt": None, "interrupt_message": None}
