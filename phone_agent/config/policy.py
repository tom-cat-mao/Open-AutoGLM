"""Versioned, immutable safety vocabulary and verification thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping
import os
import re
import unicodedata

SafetyRoute = Literal["confirm", "takeover"]

# ---------------------------------------------------------------------------
# Run resource fuses and progress evidence review
# ---------------------------------------------------------------------------

# F1: how many locate actions may run per run, and how many locate marks may
# be merged onto one screen snapshot before fail-closed rejection.
# R1 (effect-guards): the per-run locate budget is a pure runaway fuse, not a
# scarcity budget — raised 3 -> 20 because a successful locate is progress and
# must not be throttled by the same budget that used to fail-closed after 3
# successful queries. Repeated failed/effect-less locates are handled by the
# consecutive-no-effect repeat guard, not by this counter.
LOCATE_MAX_PER_RUN = 20
LOCATE_MAX_MARKS_PER_SCREEN = 5

# R1: the locate TOOL path (scoped crop input) runs LocateAnything at this
# max_size tier so scope crops keep near-native resolution instead of being
# squeezed by the observation-fallback 960 box (1216x2066 crop -> 565x960
# erased the resolution win of cropping; date digits ~38px -> ~17px broke the
# 3B model's readable-text floor). The observation fallback path keeps
# DEFAULT_LOCATEANYTHING_MAX_SIZE=960 to bound cost. Overridable at runtime
# via env PHONE_AGENT_LOCATE_LA_MAX_SIZE (read at call time by the locate
# tool) and per-call via configurable["locate_max_size"]. PIL thumbnail never
# upscales, so any crop that fits the 2048 box (all real phone scope crops)
# passes through unchanged; inputs whose short side is already <=960 are
# untouched by both tiers.
LOCATE_LA_MAX_SIZE = 2048


# R1: env-aware resolution used by the locate tool path (call-time, matching
# the factory env pattern). Invalid/empty/<=0 values fall back to the policy
# constant so a misconfigured env can never silently drop resolution below
# the 960 observation floor.
def resolve_locate_la_max_size(overrides: dict[str, Any] | None = None) -> int:
    explicit = (overrides or {}).get("locate_max_size")
    if explicit not in {None, ""}:
        try:
            resolved = int(explicit)
        except (TypeError, ValueError):
            resolved = 0
        return resolved if resolved > 0 else LOCATE_LA_MAX_SIZE
    value = os.getenv("PHONE_AGENT_LOCATE_LA_MAX_SIZE")
    if value not in {None, ""}:
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = 0
        return resolved if resolved > 0 else LOCATE_LA_MAX_SIZE
    return LOCATE_LA_MAX_SIZE

# F1/S2: when a locate query carries an optional ``scope_mark_id``, the current
# screenshot F is cropped to the scope mark's bbox (0-1000 -> device pixels)
# expanded by this ratio on EACH of the two sides (5% of the box width per
# side, 5% of the box height per side -> 110% box total), clamped to the frame,
# and the visual provider runs on the high-resolution crop instead of the
# downscaled full frame. Cropping only affects the detection region; the
# returned mark is mapped back to full-screen 0-1000 coordinates.
LOCATE_SCOPE_PADDING_RATIO = 0.05

# D2: relaxed locate-inheritance gate. A locate_N mark bound to a previous
# screen_id may be inherited (re-bound to the new screen_id) only when the
# semantic screen matches AND (the ax structure digest matches OR the 8x8 mean
# perceptual hash hamming distance is at most this many bits). The in-repo
# 8x8 mean hash is degenerate on light large-block pages (all-zero/all-one
# bitmasks), so the threshold stays at the same conservative value as
# ``perceptual_hash_max_distance`` (8): it is a tie-breaker for tiny ax-tree
# jitter, never an independent screen-identity oracle.
LOCATE_INHERIT_PHASH_MAX_DISTANCE = 8

# User-domain fuses. `max_steps` is retained by public callers as a compatibility
# input, but state/routing consume `step_cap`.
PROGRESS_EVIDENCE_HORIZON = 6
PROGRESS_EXHAUSTION_TRIGGER = 4
PROGRESS_CLAIM_GRACE_STEPS = 3
PROGRESS_CLAIM_MAX_ROUNDS = 2

# W2 T6: consecutive reflect windows in which the task_plan's current stage
# does not advance AND the trajectory is stuck, before reflect sets
# needs_recompile=True so the existing replan->goal route rebuilds the plan.
# A "window" is one reflect observation round. The plan is a belief, not a
# gate: recompiling it never blocks or finishes a task by itself.
STAGE_STALL_RECOMPILE_WINDOWS = 2

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
        "selected_object_text_match_confidence": _threshold(
            "selected_object_text_match_confidence",
            0.75,
            unit="probability",
            rationale=(
                "Selected-object text appearing after a tap shows the action landed "
                "somewhere plausible, not that the trajectory advanced or Goal moved."
            ),
            boundary="below is weak selected-object evidence",
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
        "screen_literal_max_chars": _threshold(
            "screen_literal_max_chars",
            40,
            unit="characters",
            rationale="Reject prose-shaped raw-text bindings that cannot be screen literals.",
            boundary="above is structurally unobservable",
        ),
        "binding_attestation_observations": _threshold(
            "binding_attestation_observations",
            3,
            unit="observations",
            rationale="Surface raw-text bindings that never appear in observed node text.",
            boundary="at or above degrades the contract without vetoing",
        ),
        "observation_retry_limit": _threshold(
            "observation_retry_limit",
            3,
            unit="attempts",
            rationale="Bound consecutive observation infrastructure failures.",
            boundary="at or above requires human recovery",
        ),
        "acceptance_round_limit": _threshold(
            "acceptance_round_limit",
            3,
            unit="rounds",
            rationale="Report repeated rejected finish claims independently of liveness.",
            boundary="at or above remains incomplete and replans",
        ),
        "novelty_exhaustion_steps": _threshold(
            "novelty_exhaustion_steps",
            4,
            unit="steps",
            rationale="Detect trajectories that neither advance criteria nor reach new states.",
            boundary="at or above classifies the trajectory as stuck",
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
