from dataclasses import FrozenInstanceError

import pytest

from phone_agent.config.policy import (
    DEFAULT_SAFETY_POLICY,
    DEFAULT_VERIFICATION_POLICY,
    calibration_report,
)


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("确认支付订单", "confirm"),
        ("Delete this private order", "confirm"),
        ("需要登录并输入验证码", "takeover"),
        ("Login with OTP", "takeover"),
        ("Ｌｏｇｉｎ with password", "takeover"),
    ],
)
def test_multilingual_safety_policy_routes_sensitive_text(
    text: str, route: str
) -> None:
    assert DEFAULT_SAFETY_POLICY.classify(text=text).route == route


def test_takeover_precedence_wins_over_payment_confirmation() -> None:
    result = DEFAULT_SAFETY_POLICY.classify(text="login to confirm payment")

    assert result.route == "takeover"
    assert result.category_id == "credential_or_captcha"


def test_ascii_terms_use_token_boundaries_and_do_not_match_substrings() -> None:
    assert DEFAULT_SAFETY_POLICY.classify(text="encode the payload").route is None
    assert DEFAULT_SAFETY_POLICY.classify(text="buyback analytics").route is None


def test_unknown_is_ordinary_unless_sensitive_side_effect_is_possible() -> None:
    ordinary = DEFAULT_SAFETY_POLICY.classify(text="open an unfamiliar page")
    uncertain = DEFAULT_SAFETY_POLICY.classify(
        text="unclassified submit control", may_have_sensitive_side_effect=True
    )

    assert ordinary.route is None
    assert ordinary.reason_code == "ordinary_unknown"
    assert uncertain.route == "confirm"
    assert uncertain.reason_code == "uncertain_fail_closed"


def test_semantic_tags_are_versioned_and_takeover_has_precedence() -> None:
    result = DEFAULT_SAFETY_POLICY.classify(semantic_tags=("payment", "otp"))

    assert result.route == "takeover"
    assert DEFAULT_SAFETY_POLICY.version == "safety_policy_v1"


def test_verification_policy_is_immutable_and_fully_calibrated() -> None:
    for threshold in DEFAULT_VERIFICATION_POLICY.thresholds.values():
        assert threshold.owner == "phone_agent_core"
        assert threshold.unit
        assert threshold.rationale
        assert threshold.calibration_dataset
        assert threshold.calibration_version
        assert threshold.fail_closed_boundary

    with pytest.raises(TypeError):
        DEFAULT_VERIFICATION_POLICY.thresholds["new"] = object()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        DEFAULT_VERIFICATION_POLICY.profile_id = "mutable"  # type: ignore[misc]


def test_offline_calibration_reports_errors_without_runtime_tuning() -> None:
    report = calibration_report(
        "fact_min_confidence",
        (
            {"value": 0.95, "should_accept": True},
            {"value": 0.61, "should_accept": True},
            {"value": 0.59, "should_accept": False},
            {"value": 0.2, "should_accept": False},
        ),
    )

    assert report["threshold"] == 0.6
    assert report["sample_count"] == 4
    assert report["false_accepts"] == 0
    assert report["false_rejects"] == 0
    assert DEFAULT_VERIFICATION_POLICY.value("fact_min_confidence") == 0.6
