"""Bounded privacy-safe evidence ledger written only by Reflect."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from phone_agent.config.apps import DEFAULT_APP_REGISTRY
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY
from phone_agent.graph.goal import GoalContract
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


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
    target_app_entered: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.target_app_entered is None:
            value.pop("target_app_entered")
        return value


def append_evaluation_entries(
    existing: list[dict[str, Any]] | None,
    *,
    evaluation: dict[str, Any],
    contract_id: str,
    screen_id: str | None,
    observation_epoch: int | None,
    predicate_ids: dict[str, str | None],
    target_app_entered: bool | None = None,
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
                target_app_entered=target_app_entered,
            ).to_dict()
        )
    return _bounded_entries(entries, limit=max(1, limit))


def _bounded_entries(entries: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if len(entries) <= limit:
        return entries
    anchor_indices: dict[tuple[Any, Any], int] = {}
    for index, item in enumerate(entries):
        if (
            item.get("status") == "matched"
            and item.get("source_kind") == "accessibility"
            and item.get("target_app_entered") is True
        ):
            anchor_indices[(item.get("contract_id"), item.get("criterion_id"))] = index
    anchors = set(anchor_indices.values())
    tail = [index for index in range(len(entries) - 1, -1, -1) if index not in anchors]
    selected = anchors | set(tail[: max(0, limit - len(anchors))])
    return [entries[index] for index in sorted(selected)[-limit:]]


def target_app_entered(
    contract: GoalContract,
    collected: dict[str, dict] | None,
    *,
    current_app: str | None = None,
    foreground_activity: str | None = None,
) -> bool:
    """Return whether device facts place this observation in the target app."""

    if not contract.target_app_hint:
        return True
    for criterion in contract.success_criteria:
        predicate = criterion.predicate
        if predicate is None or not predicate.predicate_id.startswith("app.foreground"):
            continue
        result = (collected or {}).get(criterion.name)
        if isinstance(result, dict) and result.get("status") == "matched":
            return True
    target = DEFAULT_APP_REGISTRY.resolve_term(contract.target_app_hint)
    if target.status != "resolved" or target.identity is None:
        return False
    observed_values = (current_app, foreground_activity)
    for value in observed_values:
        observed = str(value or "").strip()
        if not observed:
            continue
        package = observed.partition("/")[0]
        package_resolution = DEFAULT_APP_REGISTRY.resolve_package(package)
        if (
            package_resolution.status == "resolved"
            and package_resolution.identity == target.identity
        ):
            return True
        term_resolution = DEFAULT_APP_REGISTRY.resolve_term(observed)
        if term_resolution.status == "resolved" and term_resolution.identity == target.identity:
            return True
    return False


@dataclass(frozen=True)
class MilestoneLatch:
    """Plan-side milestone latch over one criterion's bounded ledger history.

    Display-only projection: "was this criterion ever satisfied at a trusted
    observation?" It is consumed by the plan agenda only. Acceptance keeps its
    own strict ``current_observation`` freshness semantics and never reads it.
    """

    latched: bool = False
    matched_epoch: int | None = None
    matched_screen_id: str | None = None


def ever_matched(
    ledger: list[dict[str, Any]],
    *,
    criterion_id: str,
    contract_id: str,
) -> MilestoneLatch:
    """Fold chronological ledger entries into a milestone latch.

    Rules (ledger is append-order, so the latest decisive entry wins):

    * ``status == "contradicted"`` → deterministic counter-evidence: unlock.
      A later positive re-observation can re-latch.
    * ``status == "matched"`` **and** ``target_app_entered is True`` → latch
      (target-app gate prevents pre-entry matches from pinning a milestone).
    * transient statuses (``stale`` / ``missing`` / ``unknown`` /
      ``unobserved``) never move the latch — this is exactly the keyboard-popup
      case that used to flip a satisfied milestone back to unsatisfied.

    ``contradicted`` after a ``matched`` unlocks; a ``matched`` after a
    ``contradicted`` re-latches; a ``matched`` without the target-app gate only
    carries the current fold and never pins.
    """

    latched = False
    matched_epoch: int | None = None
    matched_screen_id: str | None = None
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("contract_id") != contract_id:
            continue
        if str(entry.get("criterion_id") or "") != criterion_id:
            continue
        status = str(entry.get("status") or "unknown")
        if status == "contradicted":
            latched = False
            matched_epoch = None
            matched_screen_id = None
        elif (
            status == "matched"
            and entry.get("target_app_entered") is True
        ):
            latched = True
            matched_epoch = (
                int(entry["observation_epoch"])
                if isinstance(entry.get("observation_epoch"), int)
                else None
            )
            matched_screen_id = (
                str(entry.get("screen_id")) if entry.get("screen_id") else None
            )
    return MilestoneLatch(
        latched=latched,
        matched_epoch=matched_epoch,
        matched_screen_id=matched_screen_id,
    )


def unattested_raw_text_bindings(
    ledger: list[dict[str, Any]],
    contract: GoalContract,
    *,
    contract_id: str,
) -> list[str]:
    """Return raw-text bindings never observed after target-app entry."""

    window = int(
        DEFAULT_VERIFICATION_POLICY.value("binding_attestation_observations")
    )
    unattested: list[str] = []
    for criterion in contract.success_criteria:
        predicate = criterion.predicate
        if predicate is None:
            continue
        if CORE_PREDICATE_CATALOG.get(predicate.predicate_id).value_domain != "raw_text":
            continue
        entries = [
            item
            for item in ledger
            if item.get("contract_id") == contract_id
            and item.get("criterion_id") == criterion.name
            and item.get("source_kind") == "accessibility"
            and item.get("target_app_entered") is True
        ]
        if any(item.get("status") == "matched" for item in entries):
            continue
        by_observation: dict[tuple[Any, Any], dict[str, Any]] = {}
        for item in entries:
            by_observation[(item.get("screen_id"), item.get("observation_epoch"))] = item
        latest = list(by_observation.values())[-window:]
        if len(latest) == window:
            unattested.append(criterion.name)
    return unattested


def criterion_history_from_ledger(
    ledger: list[dict[str, Any]], *, contract_id: str
) -> list[dict[str, Any]]:
    """Group bounded ledger entries into per-observation criterion snapshots."""

    snapshots: dict[tuple[Any, Any], dict[str, Any]] = {}
    for item in ledger:
        if item.get("contract_id") != contract_id:
            continue
        key = (item.get("screen_id"), item.get("observation_epoch"))
        snapshot = snapshots.setdefault(
            key,
            {
                "screen_id": key[0],
                "observation_epoch": key[1],
                "per_criterion": {},
            },
        )
        snapshot["per_criterion"][str(item.get("criterion_id"))] = str(
            item.get("status") or "unknown"
        )
    return list(snapshots.values())
