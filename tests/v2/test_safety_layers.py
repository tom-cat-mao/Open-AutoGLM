"""Tests for the layered safety classifier (S2 §3): recall → reviewer → hard.

All fakes — no real device, MLX, or network. The reviewer is a stub callable so
the ``reviewer`` mode is exercised deterministically. Benchmark cases (S2 §3.8):
reviewer mode ``支付宝红包`` / ``支付方式`` / ``删除`` do NOT gate; ``确认支付`` and
a password box MUST gate.
"""

from __future__ import annotations

from types import SimpleNamespace

from phone_agent.v2.middleware.safety import (
    ToolCallVerdict,
    build_hitl_middleware,
    classify_tool_call,
    is_sensitive_tool_call,
)


def _request(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args})


class _Cfg:
    def __init__(self, mode: str = "hard", reviewer_model=None, verifier_model=None):
        self.safety_mode = mode
        self.safety_reviewer_model = reviewer_model
        self.verifier_model = verifier_model


def _reversible_reviewer(tool_name: str, text: str) -> bool:
    """Stub reviewer: everything reversible (no gate)."""

    return True


def _irreversible_reviewer(tool_name: str, text: str) -> bool:
    """Stub reviewer: everything irreversible (gate)."""

    return False


def _throwing_reviewer(tool_name: str, text: str) -> bool:
    raise RuntimeError("reviewer network down")


# --------------------------------------------------------------------------
# hard gate: irreversible commit (commit term + irreversible object)
# --------------------------------------------------------------------------
def test_irreversible_commit_gates_in_every_mode():
    for mode in ("hard", "reviewer"):
        v = classify_tool_call(
            _request("tap", {"target_description": "确认支付"}),
            None,
            _Cfg(mode),
            reviewer=_reversible_reviewer,
        )
        assert v.should_gate is True
        assert v.level == "hard"
        assert v.reason == "irreversible_commit"


def test_irreversible_commit_variants():
    for text in ("确认支付", "立即支付", "发送红包", "确认删除", "提交订单", "confirm payment"):
        v = classify_tool_call(_request("tap", {"target_description": text}), None, _Cfg("hard"))
        assert v.should_gate is True, text
        assert v.level == "hard"


def test_type_text_confirm_pay_gates():
    v = classify_tool_call(_request("type_text", {"text": "确认支付"}), None, _Cfg("hard"))
    assert v.should_gate is True
    assert v.level == "hard"


# --------------------------------------------------------------------------
# benchmark: soft candidates must NOT gate (recall != popup)
# --------------------------------------------------------------------------
def test_benchmark_soft_candidates_pass_in_hard_mode():
    for text in ("支付宝红包", "支付方式", "删除"):
        v = classify_tool_call(_request("tap", {"target_description": text}), None, _Cfg("hard"))
        assert v.should_gate is False, text
        assert v.level == "recall"


def test_benchmark_soft_candidates_pass_in_reviewer_mode_when_reversible():
    for text in ("支付宝红包", "支付方式", "删除"):
        v = classify_tool_call(
            _request("tap", {"target_description": text}),
            None,
            _Cfg("reviewer"),
            reviewer=_reversible_reviewer,
        )
        assert v.should_gate is False, text
        assert v.level == "reviewer"
        assert v.reason == "reviewer_reversible"


# --------------------------------------------------------------------------
# reviewer layer: reversibility decision + fail-closed
# --------------------------------------------------------------------------
def test_reviewer_irreversible_gates():
    v = classify_tool_call(
        _request("tap", {"target_description": "支付方式"}),
        None,
        _Cfg("reviewer"),
        reviewer=_irreversible_reviewer,
    )
    assert v.should_gate is True
    assert v.level == "reviewer"
    assert v.reason == "reviewer_irreversible"


def test_reviewer_exception_is_fail_closed():
    v = classify_tool_call(
        _request("tap", {"target_description": "支付方式"}),
        None,
        _Cfg("reviewer"),
        reviewer=_throwing_reviewer,
    )
    assert v.should_gate is True
    assert v.reason == "reviewer_error_failclosed"


def test_reviewer_unavailable_is_fail_closed():
    # reviewer mode but no reviewer callable -> soft candidates fail-closed (gate).
    v = classify_tool_call(
        _request("tap", {"target_description": "支付方式"}),
        None,
        _Cfg("reviewer"),
        reviewer=None,
    )
    assert v.should_gate is True
    assert v.reason == "reviewer_unavailable_failclosed"


# --------------------------------------------------------------------------
# password box + credential input (hard, always)
# --------------------------------------------------------------------------
def test_password_field_type_text_gates():
    mark = SimpleNamespace(password=True, text_summary=None)
    session = SimpleNamespace(marks={"ax_1": mark})
    v = classify_tool_call(
        _request("type_text", {"text": "hunter2", "target_mark_id": "ax_1"}),
        session,
        _Cfg("hard"),
    )
    assert v.should_gate is True
    assert v.level == "hard"
    assert v.reason == "password_field"


def test_non_password_field_ordinary_text_passes():
    mark = SimpleNamespace(password=False, text_summary="搜索框")
    session = SimpleNamespace(marks={"ax_1": mark})
    v = classify_tool_call(
        _request("type_text", {"text": "北京天气", "target_mark_id": "ax_1"}),
        session,
        _Cfg("hard"),
    )
    assert v.should_gate is False
    assert v.level == "none"


def test_credential_text_gates_by_pattern():
    # SENSITIVE_PATTERN: a verification-code shape -> credential input -> hard.
    v = classify_tool_call(_request("type_text", {"text": "验证码 887766"}), None, _Cfg("hard"))
    assert v.should_gate is True
    assert v.reason == "credential_input"


def test_credential_text_gates_by_policy_vocab():
    v = classify_tool_call(_request("type_text", {"text": "输入登录密码"}), None, _Cfg("hard"))
    assert v.should_gate is True
    assert v.reason == "credential_input"


# --------------------------------------------------------------------------
# self-declared sensitivity always escalates
# --------------------------------------------------------------------------
def test_self_declared_sensitive_escalates():
    v = classify_tool_call(
        _request("tap", {"target_description": "返回首页", "sensitive": True}),
        None,
        _Cfg("hard"),
    )
    assert v.should_gate is True
    assert v.reason == "self_declared"


# --------------------------------------------------------------------------
# launch_app softened out of the default gate (§3.6)
# --------------------------------------------------------------------------
def test_launch_app_soft_in_hard_mode_passes():
    v = classify_tool_call(_request("launch_app", {"app_name": "招商银行"}), None, _Cfg("hard"))
    assert v.should_gate is False
    assert v.level == "recall"
    assert v.reason == "sensitive_app_soft_pass"


def test_launch_app_reviewer_mode_delegates():
    v = classify_tool_call(
        _request("launch_app", {"app_name": "Alipay"}),
        None,
        _Cfg("reviewer"),
        reviewer=_reversible_reviewer,
    )
    assert v.should_gate is False
    assert v.level == "reviewer"


def test_launch_app_ordinary_no_candidate():
    v = classify_tool_call(_request("launch_app", {"app_name": "相机"}), None, _Cfg("hard"))
    assert v.should_gate is False
    assert v.level == "none"


# --------------------------------------------------------------------------
# off mode: no actuation gate; control tools still handled elsewhere
# --------------------------------------------------------------------------
def test_off_mode_passes_everything_actuation():
    for name, args in (
        ("tap", {"target_description": "确认支付"}),
        ("type_text", {"text": "验证码 887766"}),
        ("launch_app", {"app_name": "招商银行"}),
    ):
        v = classify_tool_call(_request(name, args), None, _Cfg("off"))
        assert v.should_gate is False
        assert v.reason == "mode_off"


def test_off_mode_take_over_still_interrupts_via_middleware():
    # take_over / ask_user are control interrupts wired unconditionally in the
    # middleware, independent of safety_mode.
    mw = build_hitl_middleware(session=None, config=_Cfg("off"))
    assert "when" not in mw.interrupt_on["take_over"]
    assert mw.interrupt_on["ask_user"]["allowed_decisions"] == ["respond"]


# --------------------------------------------------------------------------
# non-actuation tools never gate
# --------------------------------------------------------------------------
def test_non_actuation_tool_passes():
    v = classify_tool_call(_request("read_screen", {}), None, _Cfg("hard"))
    assert v.should_gate is False
    assert v.reason == "not_actuation"


# --------------------------------------------------------------------------
# backward-compat wrapper
# --------------------------------------------------------------------------
def test_is_sensitive_wrapper_reports_hard_gate_only():
    assert is_sensitive_tool_call(_request("tap", {"target_description": "确认支付"})) is True
    # soft candidate -> hard mode -> no gate.
    assert is_sensitive_tool_call(_request("tap", {"target_description": "支付方式"})) is False


# --------------------------------------------------------------------------
# build_hitl_middleware signature compatibility (iron rule)
# --------------------------------------------------------------------------
def test_build_hitl_middleware_signature_compat():
    # All three legacy/new call shapes must build without error.
    assert build_hitl_middleware() is not None
    assert build_hitl_middleware(session=None) is not None
    assert build_hitl_middleware(session=None, config=_Cfg("hard")) is not None


def test_verdict_dataclass_shape():
    v = ToolCallVerdict(should_gate=True, level="hard", route="confirm", reason="x")
    assert v.should_gate is True
    assert v.level == "hard"
    assert v.route == "confirm"
    assert v.reason == "x"
