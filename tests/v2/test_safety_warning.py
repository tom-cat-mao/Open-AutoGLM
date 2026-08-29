"""Tests for the U2 warning-flow safety system.

The default safety mode is ``wary``: a risky execution call is intercepted with
a warning ToolMessage (not executed, no human interrupt); the model resends with
``confirm_irreversible=true`` to act. ``hard`` keeps the legacy HITL interrupt;
``off`` disables the gate. ``ask_user``/``take_over`` always interrupt.

All fakes — no real device, MLX, or network.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from phone_agent.v2.middleware.safety import (
    SafetyWarningMiddleware,
    build_hitl_middleware,
    build_safety_warning_middleware,
    format_warning,
    classify_tool_call,
)


class _Cfg:
    def __init__(self, mode: str = "wary", reviewer_model=None, verifier_model=None):
        self.safety_mode = mode
        self.safety_reviewer_model = reviewer_model
        self.verifier_model = verifier_model


def _req(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call_1"})


def _executed_handler(counter: dict):
    def handler(request):  # noqa: ANN001
        counter["n"] += 1
        return ToolMessage(
            content="OK. 已点击", tool_call_id="call_1", name="tap", status="success"
        )

    return handler


def _mw(mode: str = "wary", notes: list | None = None) -> SafetyWarningMiddleware:
    notify = (lambda m: notes.append(m)) if notes is not None else (lambda m: None)
    return SafetyWarningMiddleware(None, _Cfg(mode), notify=notify)


# --------------------------------------------------------------------------
# core warning flow: warn-not-execute, then confirm-to-execute
# --------------------------------------------------------------------------
def test_risky_call_is_warned_not_executed():
    counter = {"n": 0}
    notes: list = []
    mw = _mw("wary", notes)
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "确认支付", "intent": "支付订单"}),
        _executed_handler(counter),
    )
    # No device action ran; a warning ToolMessage is returned instead.
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "confirm_irreversible=true" in result.content
    assert "未执行" in result.content
    # Non-blocking harness notice was emitted (stdout channel).
    assert notes and "irreversible_commit" in notes[0]


def test_confirm_irreversible_resend_executes():
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req(
            "tap",
            {"target_description": "确认支付", "confirm_irreversible": True},
        ),
        _executed_handler(counter),
    )
    # The confirmed resend passes straight through to the tool.
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


def test_benign_call_executes_without_warning():
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "返回首页"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


# --------------------------------------------------------------------------
# self-declared sensitivity always triggers the warning flow
# --------------------------------------------------------------------------
def test_self_declared_sensitive_triggers_warning():
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "返回首页", "sensitive": True}),
        _executed_handler(counter),
    )
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


def test_self_declared_then_confirm_executes():
    counter = {"n": 0}
    mw = _mw("wary")
    # Self-declared but also confirmed -> executes (confirm short-circuits first).
    result = mw.wrap_tool_call(
        _req(
            "tap",
            {"target_description": "返回首页", "sensitive": True, "confirm_irreversible": True},
        ),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


# --------------------------------------------------------------------------
# password / credential input warns in wary mode
# --------------------------------------------------------------------------
def test_password_field_warns_in_wary_mode():
    mark = SimpleNamespace(password=True, text_summary=None)
    session = SimpleNamespace(marks={"ax_1": mark})
    mw = SafetyWarningMiddleware(session, _Cfg("wary"), notify=lambda m: None)
    counter = {"n": 0}
    result = mw.wrap_tool_call(
        _req("type_text", {"text": "hunter2", "target_mark_id": "ax_1"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)
    assert "密码" in result.content


def test_credential_text_warns_in_wary_mode():
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("type_text", {"text": "验证码 887766"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)


# --------------------------------------------------------------------------
# three-tier mode wiring: wary | hard | off
# --------------------------------------------------------------------------
def test_wary_mode_builds_warning_middleware_no_actuation_interrupt():
    warn = build_safety_warning_middleware(None, _Cfg("wary"))
    assert isinstance(warn, SafetyWarningMiddleware)
    hitl = build_hitl_middleware(None, _Cfg("wary"))
    # wary: actuation tools are NOT interrupted by HITL (warning flow owns them).
    assert "tap" not in hitl.interrupt_on
    assert set(hitl.interrupt_on) == {"ask_user", "take_over"}


def test_hard_mode_builds_no_warning_middleware_but_interrupts_actuation():
    warn = build_safety_warning_middleware(None, _Cfg("hard"))
    assert warn is None
    hitl = build_hitl_middleware(None, _Cfg("hard"))
    assert "tap" in hitl.interrupt_on
    assert callable(hitl.interrupt_on["tap"]["when"])


def test_off_mode_builds_no_warning_middleware_and_no_actuation_interrupt():
    warn = build_safety_warning_middleware(None, _Cfg("off"))
    assert warn is None
    hitl = build_hitl_middleware(None, _Cfg("off"))
    assert "tap" not in hitl.interrupt_on
    assert set(hitl.interrupt_on) == {"ask_user", "take_over"}


def test_off_mode_warning_middleware_passthrough_if_ever_built():
    # Defense in depth: even if an off-mode warning middleware existed, classify
    # returns mode_off so nothing is ever blocked.
    counter = {"n": 0}
    mw = SafetyWarningMiddleware(None, _Cfg("off"), notify=lambda m: None)
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "确认支付"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


# --------------------------------------------------------------------------
# ask_user / take_over still interrupt in every mode (control, not safety)
# --------------------------------------------------------------------------
def test_ask_user_and_take_over_still_interrupt_in_wary():
    hitl = build_hitl_middleware(None, _Cfg("wary"))
    assert hitl.interrupt_on["ask_user"]["allowed_decisions"] == ["respond"]
    assert "when" not in hitl.interrupt_on["take_over"]


def test_ask_user_and_take_over_still_interrupt_in_off():
    hitl = build_hitl_middleware(None, _Cfg("off"))
    assert hitl.interrupt_on["ask_user"]["allowed_decisions"] == ["respond"]
    assert "when" not in hitl.interrupt_on["take_over"]


def test_warning_middleware_never_touches_control_tools():
    # ask_user / take_over are non-actuation -> warning middleware passes them.
    counter = {"n": 0}
    mw = _mw("wary")

    def handler(request):  # noqa: ANN001
        counter["n"] += 1
        return ToolMessage(content="[ASK_USER] q", tool_call_id="call_1", name="ask_user")

    result = mw.wrap_tool_call(_req("ask_user", {"question": "确认支付吗?"}), handler)
    assert counter["n"] == 1
    assert result.content == "[ASK_USER] q"


# --------------------------------------------------------------------------
# reviewer mode: soft candidate precision, warning path
# --------------------------------------------------------------------------
def test_reviewer_mode_irreversible_soft_candidate_warns():
    def reviewer(tool_name, text):  # noqa: ANN001
        return False  # everything irreversible -> warn

    mw = SafetyWarningMiddleware(None, _Cfg("reviewer"), reviewer=reviewer, notify=lambda m: None)
    counter = {"n": 0}
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "支付方式"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)


def test_reviewer_mode_reversible_soft_candidate_executes():
    def reviewer(tool_name, text):  # noqa: ANN001
        return True  # reversible -> no warning

    mw = SafetyWarningMiddleware(None, _Cfg("reviewer"), reviewer=reviewer, notify=lambda m: None)
    counter = {"n": 0}
    result = mw.wrap_tool_call(
        _req("tap", {"target_description": "支付方式"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


# --------------------------------------------------------------------------
# format_warning shape
# --------------------------------------------------------------------------
def test_format_warning_contains_world_fact_and_options():
    verdict = classify_tool_call(_req("tap", {"target_description": "确认支付"}), None, _Cfg("wary"))
    text = format_warning("tap", {"target_description": "确认支付"}, verdict)
    assert "世界事实" in text
    assert "选项" in text
    assert "confirm_irreversible=true" in text
    assert "ask_user" in text and "take_over" in text


def test_launch_app_sensitive_soft_candidate_does_not_warn_in_wary():
    # launch_app is reversible (back/home exits) -> a bank-app launch is a SOFT
    # candidate. In wary mode soft candidates do NOT warn (召回≠预警); it only
    # warns in reviewer mode if the reviewer judges it irreversible.
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("launch_app", {"app_name": "招商银行"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
    assert result.content == "OK. 已点击"


def test_launch_app_self_declared_warns_in_wary():
    # Self-declaration escalates even a reversible launch to the warning flow.
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("launch_app", {"app_name": "招商银行", "sensitive": True}),
        _executed_handler(counter),
    )
    assert counter["n"] == 0
    assert isinstance(result, ToolMessage)


def test_launch_app_ordinary_executes_in_wary():
    counter = {"n": 0}
    mw = _mw("wary")
    result = mw.wrap_tool_call(
        _req("launch_app", {"app_name": "相机"}),
        _executed_handler(counter),
    )
    assert counter["n"] == 1
