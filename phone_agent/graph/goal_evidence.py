"""Bounded privacy-safe evidence ledger written only by Reflect."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


CriterionEvidenceStatus = Literal[
    "invalid", "contradicted", "stale", "missing", "unknown", "unobserved", "matched"
]


@dataclass(frozen=True)
class CriterionEvidenceEntry:
    """Safe match outcome; raw expected/observed values are never stored."""

    criterion_id: str
    predicate_id: str | None
    status: CriterionEvidenceStatus
    reason_code: str
    source_kind: str | None
    confidence_bucket: str | None
    contract_id: str
    screen_id: str | None
    observation_epoch: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_evaluation_entries(
    existing: list[dict[str, Any]] | None,
    *,
    evaluation: dict[str, Any],
    contract_id: str,
    screen_id: str | None,
    observation_epoch: int | None,
    predicate_ids: dict[str, str | None],
    limit: int = 64,
) -> list[dict[str, Any]]:
    """Append safe per-criterion results from one Reflect evaluation."""

    entries = list(existing or [])
    per_criterion = (evaluation.get("evidence") or {}).get("per_criterion") or {}
    for criterion_id, result in per_criterion.items():
        if not isinstance(result, dict):
            continue
        raw_status = str(result.get("status") or "unknown")
        status: CriterionEvidenceStatus = (
            raw_status
            if raw_status
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
        )  # type: ignore[assignment]
        entries.append(
            CriterionEvidenceEntry(
                criterion_id=str(criterion_id),
                predicate_id=predicate_ids.get(str(criterion_id)),
                status=status,
                reason_code=str(result.get("reason") or "unspecified")[:64],
                source_kind=(
                    str(result.get("source"))[:32] if result.get("source") else None
                ),
                confidence_bucket=(
                    str(result.get("confidence_bucket"))[:16]
                    if result.get("confidence_bucket")
                    else None
                ),
                contract_id=contract_id,
                screen_id=screen_id,
                observation_epoch=observation_epoch,
            ).to_dict()
        )
    return entries[-max(1, limit) :]
