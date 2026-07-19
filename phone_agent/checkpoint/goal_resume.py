"""Trusted Goal progress projection and fail-closed checkpoint rehydration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from typing import Any

from phone_agent.graph.goal import GoalContract
from phone_agent.graph.goal_requirements import TaskRequirementSet
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG

RESUME_SCHEMA_VERSION = "goal_resume_v1"
NORMALIZER_VERSION = "goal_canonical_json_v1"


@dataclass(frozen=True)
class GoalResumeProjection:
    schema_version: str
    normalizer_version: str
    contract_binding: str
    progress: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "normalizer_version": self.normalizer_version,
            "contract_binding": self.contract_binding,
            "progress": [dict(item) for item in self.progress],
        }


@dataclass(frozen=True)
class GoalResumeResult:
    status: str
    evidence_ledger: tuple[dict[str, Any], ...]
    requires_reobservation: bool
    reason_code: str


class TrustedGoalResumeBinder:
    """Issue and verify keyed bindings; the secret never enters state/checkpoints."""

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("trusted resume secret must contain at least 32 bytes")
        self._secret = secret

    def binding(self, requirements: TaskRequirementSet, contract: GoalContract) -> str:
        payload = _canonical_payload(requirements, contract)
        digest = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return f"hmac-sha256-v1:{digest}"

    def build_projection(
        self,
        *,
        requirements: TaskRequirementSet,
        contract: GoalContract,
        evidence_ledger: list[dict[str, Any]],
    ) -> GoalResumeProjection:
        binding = self.binding(requirements, contract)
        criteria = {item.name: item for item in contract.success_criteria}
        progress: list[dict[str, Any]] = []
        for entry in evidence_ledger:
            if not isinstance(entry, dict):
                continue
            criterion = criteria.get(str(entry.get("criterion_id") or ""))
            if criterion is None or criterion.scope != "trajectory":
                continue
            predicate = criterion.predicate
            if predicate is None:
                continue
            definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
            if definition.projection.persistence != "checkpoint_safe":
                continue
            if entry.get("predicate_id") != predicate.predicate_id:
                continue
            progress.append(
                {
                    "criterion_id": criterion.name,
                    "predicate_id": predicate.predicate_id,
                    "status": _safe_status(entry.get("status")),
                    "reason_code": _safe_id(entry.get("reason_code"), "restored"),
                    "source_kind": _safe_id(entry.get("source_kind"), "unknown"),
                    "confidence_bucket": _safe_id(
                        entry.get("confidence_bucket"), "unknown"
                    ),
                    "screen_id": _safe_id(entry.get("screen_id"), "restored"),
                    "observation_epoch": max(
                        0, int(entry.get("observation_epoch") or 0)
                    ),
                }
            )
        return GoalResumeProjection(
            schema_version=RESUME_SCHEMA_VERSION,
            normalizer_version=NORMALIZER_VERSION,
            contract_binding=binding,
            progress=tuple(progress[-64:]),
        )

    def rehydrate(
        self,
        projection: dict[str, Any] | GoalResumeProjection | None,
        *,
        requirements: TaskRequirementSet,
        contract: GoalContract,
    ) -> GoalResumeResult:
        value = (
            projection.to_dict()
            if isinstance(projection, GoalResumeProjection)
            else projection
        )
        if not isinstance(value, dict):
            return _invalid("trusted_projection_missing")
        if value.get("schema_version") != RESUME_SCHEMA_VERSION:
            return _invalid("resume_schema_mismatch")
        if value.get("normalizer_version") != NORMALIZER_VERSION:
            return _invalid("normalizer_version_mismatch")
        expected = self.binding(requirements, contract)
        actual = str(value.get("contract_binding") or "")
        if not hmac.compare_digest(actual, expected):
            return _invalid("contract_binding_mismatch")

        criteria = {item.name: item for item in contract.success_criteria}
        restored: list[dict[str, Any]] = []
        for entry in value.get("progress") or []:
            if not isinstance(entry, dict):
                continue
            criterion = criteria.get(str(entry.get("criterion_id") or ""))
            if criterion is None or criterion.scope != "trajectory":
                continue
            predicate = criterion.predicate
            if predicate is None:
                continue
            definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
            if definition.projection.persistence != "checkpoint_safe":
                continue
            if entry.get("predicate_id") != predicate.predicate_id:
                continue
            restored.append(
                {
                    "criterion_id": criterion.name,
                    "predicate_id": predicate.predicate_id,
                    "status": _safe_status(entry.get("status")),
                    "reason_code": _safe_id(entry.get("reason_code"), "restored"),
                    "source_kind": _safe_id(entry.get("source_kind"), "unknown"),
                    "confidence_bucket": _safe_id(
                        entry.get("confidence_bucket"), "unknown"
                    ),
                    "contract_id": contract.task_hash,
                    "screen_id": _safe_id(entry.get("screen_id"), "restored"),
                    "observation_epoch": max(
                        0, int(entry.get("observation_epoch") or 0)
                    ),
                }
            )
        return GoalResumeResult(
            status="trusted",
            evidence_ledger=tuple(restored[-64:]),
            requires_reobservation=True,
            reason_code="trusted_trajectory_progress_restored",
        )


def _canonical_payload(
    requirements: TaskRequirementSet, contract: GoalContract
) -> bytes:
    criteria = []
    for item in contract.success_criteria:
        criteria.append(
            {
                "criterion_id": item.name,
                "required": item.required,
                "scope": item.scope,
                "predicate": (
                    {
                        "predicate_id": item.predicate.predicate_id,
                        "matcher_id": item.predicate.matcher_id,
                        "privacy_class": item.predicate.privacy_class,
                        "expected_value": item.predicate.expected_value,
                    }
                    if item.predicate is not None
                    else None
                ),
                "allowed_sources": list(item.allowed_sources),
                "freshness": item.freshness,
                "ambiguity_policy": item.ambiguity_policy,
                "dependencies": list(item.dependencies),
                "contradictions": list(item.contradictions),
            }
        )
    payload = {
        "schema_version": RESUME_SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "requirements": asdict(requirements),
        "contract": {
            "task_hash": contract.task_hash,
            "redacted_objective": contract.redacted_objective,
            "objective_length": contract.objective_length,
            "criteria": criteria,
            "constraints": contract.constraints,
            "non_goals": contract.non_goals,
            "target_app_hint": contract.target_app_hint,
            "target_activity_hint": contract.target_activity_hint,
            "ordinal": contract.ordinal,
            "entities_sha": contract.entities_sha,
            "verification_strategy": contract.verification_strategy,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _invalid(reason_code: str) -> GoalResumeResult:
    return GoalResumeResult(
        status="goal_contract_invalid",
        evidence_ledger=(),
        requires_reobservation=True,
        reason_code=reason_code,
    )


def _safe_id(value: Any, default: str) -> str:
    text = str(value or default)
    safe = "".join(char if char.isalnum() or char in "_.:-" else "-" for char in text)
    return safe[:64] or default


def _safe_status(value: Any) -> str:
    status = str(value or "unknown")
    return (
        status
        if status
        in {
            "invalid",
            "contradicted",
            "stale",
            "missing",
            "unknown",
            "unobserved",
            "matched",
        }
        else "unknown"
    )
