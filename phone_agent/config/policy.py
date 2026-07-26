"""Versioned, immutable safety vocabulary and verification thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping
import re
import unicodedata

SafetyRoute = Literal["confirm", "takeover"]


@dataclass(frozen=True)
class SafetyCategory:
    category_id: str
    route: SafetyRoute
    precedence: int
    terms: tuple[str, ...]
    semantic_tags: tuple[str, ...]


@dataclass(frozen=True)
class SafetyClassification:
    route: SafetyRoute | None
    category_id: str | None
    reason_code: str


class SafetyPolicyRegistry:
    """Core-owned multilingual classification with fixed precedence."""

    def __init__(self, categories: tuple[SafetyCategory, ...], *, version: str) -> None:
        self.version = version
        self._categories = tuple(
            sorted(categories, key=lambda item: item.precedence, reverse=True)
        )
        ids = {item.category_id for item in categories}
        if len(ids) != len(categories):
            raise ValueError("duplicate safety category")

    @property
    def categories(self) -> tuple[SafetyCategory, ...]:
        return self._categories

    @property
    def semantic_tags(self) -> frozenset[str]:
        return frozenset(
            tag for category in self._categories for tag in category.semantic_tags
        )

    def classify(
        self,
        *,
        text: str = "",
        semantic_tags: tuple[str, ...] = (),
        may_have_sensitive_side_effect: bool = False,
    ) -> SafetyClassification:
        normalized_text = unicodedata.normalize("NFKC", str(text)).casefold()
        normalized_tags = {
            unicodedata.normalize("NFKC", str(tag)).casefold() for tag in semantic_tags
        }
        for category in self._categories:
            if normalized_tags.intersection(category.semantic_tags) or any(
                _term_matches(term, normalized_text) for term in category.terms
            ):
                return SafetyClassification(
                    category.route, category.category_id, "policy_match"
                )
        if may_have_sensitive_side_effect:
            return SafetyClassification(
                "confirm", "uncertain_sensitive_side_effect", "uncertain_fail_closed"
            )
        return SafetyClassification(None, None, "ordinary_unknown")


@dataclass(frozen=True)
class ThresholdSpec:
    name: str
    value: float
    owner: str
    unit: str
    rationale: str
    calibration_dataset: str
    calibration_version: str
    fail_closed_boundary: str


@dataclass(frozen=True)
class VerificationPolicy:
    profile_id: str
    version: str
    thresholds: Mapping[str, ThresholdSpec]
    source_priority: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "thresholds", MappingProxyType(dict(self.thresholds)))
        for name, threshold in self.thresholds.items():
            if name != threshold.name or not threshold.owner or not threshold.rationale:
                raise ValueError("threshold metadata is incomplete")
            if not threshold.calibration_dataset or not threshold.calibration_version:
                raise ValueError("threshold calibration metadata is incomplete")

    def value(self, name: str) -> float:
        try:
            return self.thresholds[name].value
        except KeyError as exc:
            raise ValueError(f"unknown verification threshold: {name}") from exc


DEFAULT_SAFETY_POLICY = SafetyPolicyRegistry(
    (
        SafetyCategory(
            "credential_or_captcha",
            "takeover",
            200,
            (
                "login",
                "password",
                "captcha",
                "otp",
                "verification code",
                "account",
                "登录",
                "密码",
                "验证码",
                "账户",
                "账号",
            ),
            ("login", "password", "captcha", "otp"),
        ),
        SafetyCategory(
            "sensitive_side_effect",
            "confirm",
            100,
            (
                "pay",
                "payment",
                "purchase",
                "buy",
                "order",
                "confirm",
                "delete",
                "remove",
                "permission",
                "privacy",
                "支付",
                "付款",
                "购买",
                "下单",
                "确认",
                "删除",
                "移除",
                "权限",
                "隐私",
            ),
            ("payment", "privacy", "delete", "permission"),
        ),
    ),
    version="safety_policy_v1",
)


def _threshold(
    name: str, value: float, *, unit: str, rationale: str, boundary: str
) -> ThresholdSpec:
    return ThresholdSpec(
        name=name,
        value=value,
        owner="phone_agent_core",
        unit=unit,
        rationale=rationale,
        calibration_dataset="tests/fixtures/policy_calibration_v1.json",
        calibration_version="v1",
        fail_closed_boundary=boundary,
    )


DEFAULT_VERIFICATION_POLICY = VerificationPolicy(
    profile_id="conservative_default",
    version="verification_policy_v1",
    thresholds={
        "mark_min_confidence": _threshold(
            "mark_min_confidence",
            0.3,
            unit="probability",
            rationale="Reject weak grounding candidates before execution.",
            boundary="below rejects grounding",
        ),
        "perceptual_hash_max_distance": _threshold(
            "perceptual_hash_max_distance",
            8,
            unit="hamming_bits",
            rationale="Reject mark registries from materially different screenshots.",
            boundary="above rejects binding",
        ),
        "fact_min_confidence": _threshold(
            "fact_min_confidence",
            0.6,
            unit="probability",
            rationale="Exclude weak facts from Goal authority resolution.",
            boundary="below resolves as no evidence",
        ),
        "verified_reflection_skip_confidence": _threshold(
            "verified_reflection_skip_confidence",
            0.9,
            unit="probability",
            rationale="Skip model reflection only for high-confidence deterministic verification.",
            boundary="below requires reflection",
        ),
        "takeover_retry_count": _threshold(
            "takeover_retry_count",
            3,
            unit="attempts",
            rationale="Bound repeated failures before human takeover.",
            boundary="at or above routes takeover",
        ),
        "selected_object_text_match_confidence": _threshold(
            "selected_object_text_match_confidence",
            0.75,
            unit="probability",
            rationale=(
                "Selected-object text appearing after a tap shows the action landed "
                "somewhere plausible, not that the trajectory advanced. Held below "
                "verified_reflection_skip_confidence so model reflection still runs."
            ),
            boundary="below verified_reflection_skip_confidence keeps reflection",
        ),
        "repeated_action_threshold": _threshold(
            "repeated_action_threshold",
            2,
            unit="attempts",
            rationale=(
                "Repeating one target on one surface this many prior times is a loop "
                "even when every step verified as successful."
            ),
            boundary="at or above reports repeated_action",
        ),
    },
    source_priority=(
        "device",
        "external_probe",
        "accessibility",
        "screen_object",
        "visual_region",
        "mark",
        "whole_screen",
    ),
)


def calibration_report(
    threshold_name: str,
    samples: tuple[dict[str, Any], ...],
    *,
    policy: VerificationPolicy = DEFAULT_VERIFICATION_POLICY,
) -> dict[str, Any]:
    """Evaluate a fixed threshold over offline labeled fixtures; never tune it."""

    threshold = policy.value(threshold_name)
    false_accepts = 0
    false_rejects = 0
    for sample in samples:
        accepted = float(sample["value"]) >= threshold
        expected = bool(sample["should_accept"])
        false_accepts += int(accepted and not expected)
        false_rejects += int(not accepted and expected)
    return {
        "profile_id": policy.profile_id,
        "policy_version": policy.version,
        "threshold_name": threshold_name,
        "threshold": threshold,
        "sample_count": len(samples),
        "false_accepts": false_accepts,
        "false_rejects": false_rejects,
    }


def _term_matches(term: str, text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", term).casefold()
    if normalized.isascii() and normalized.replace(" ", "").isalpha():
        return (
            re.search(rf"(?<![a-z]){re.escape(normalized)}(?![a-z])", text) is not None
        )
    return normalized in text
