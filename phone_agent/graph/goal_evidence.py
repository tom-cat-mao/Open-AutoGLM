"""Bounded privacy-safe evidence ledger written only by Reflect.

Stage-Sealing acceptance layer (A/B phases): the ledger grows three new
entry kinds beside the original per-criterion evaluation entries:

* ``screen_text_digest`` (L1) — top-N mechanically extracted accessibility
  texts per screen; zero model cost; the substrate for ``L1 closure``
  (a raw-text criterion is settled once its expected value was recorded at a
  trusted target-app observation).
* ``effect_event`` (L2) — one record per succeeded/partial action
  (idempotent per screen+step); judge context, never a direct satisfaction
  source on its own.
* ``stage_seal`` / ``stage_unseal`` — stage sealing (Phase B): a stage whose
  done criteria are all latched is sealed once; sealed criteria are
  authoritative in later folds until a positive counter-observation revokes
  the seal.

Privacy: every text that enters the ledger is regex-redacted
(``redact_context_text``) on write; the trace/checkpoint egress policy is
unchanged because the ledger only ever carries redacted forms.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
import re
import unicodedata
from typing import Any, Literal

from phone_agent.config.apps import DEFAULT_APP_REGISTRY
from phone_agent.config.policy import DEFAULT_VERIFICATION_POLICY
from phone_agent.graph.context import redact_context_text
from phone_agent.graph.goal import GoalContract
from phone_agent.graph.marks import ACCESSIBILITY_MARK_SOURCES
from phone_agent.graph.predicates import CORE_PREDICATE_CATALOG


# ----------------------------------------------------------------------
# Stage-Sealing policy constants (env-overridable, read at call time)
# ----------------------------------------------------------------------

# L1: per-screen top-N accessibility texts kept in one digest entry.
L1_DIGEST_TEXT_LIMIT = 40
# L1: how many recent screens of digests the bounded ledger keeps.
L1_DIGEST_SCREEN_WINDOW = 30
# L2: how many recent effect events the bounded ledger keeps.
L2_EFFECT_EVENT_LIMIT = 24
# Stage seals/unseals kept (active seal set is small; window bounds state size).
SEAL_LEDGER_LIMIT = 32
# One digest text is truncated to this many chars on write.
L1_DIGEST_TEXT_MAX_CHARS = 160


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def l1_digest_text_limit() -> int:
    return _env_int("PHONE_AGENT_L1_TEXT_LIMIT", L1_DIGEST_TEXT_LIMIT)


def l1_digest_screen_window() -> int:
    return _env_int("PHONE_AGENT_L1_SCREEN_WINDOW", L1_DIGEST_SCREEN_WINDOW)


# ----------------------------------------------------------------------
# Semantic keys (name-independent identity, Phase B recompile inheritance)
# ----------------------------------------------------------------------

# Literals whose presence in a TERMINAL criterion's description signals the
# criterion may not be observable on the final screen (compile-time warning).
FULL_DATE_LITERAL_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
INTERVAL_LITERAL_RE = re.compile(r"\d{1,2}:\d{2}\s*[-~]\s*\d{1,2}:\d{2}")


def normalize_semantic_text(text: str | None) -> str:
    """NFKC + casefold + whitespace collapse; name-independent normalization."""

    value = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(value.split()).strip().casefold()


def criterion_semantic_key(description: str) -> str:
    """Criterion-level semantic key: hash of the normalized description."""

    normalized = normalize_semantic_text(description)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _criterion_description(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("description") or "")
    return str(getattr(value, "description", "") or "")


def stage_semantic_key(stage: Any, criteria: dict[str, Any]) -> str:
    """Stage-level semantic key: hash of the sorted normalized descriptions of
    the stage's done criteria. Independent of criterion names, so a recompile
    that renames criteria (same descriptions) keeps the same key."""

    descriptions = sorted(
        normalize_semantic_text(_criterion_description(criteria[name]))
        for name in (getattr(stage, "done_criteria", None) or ())
        if name in criteria
    )
    return hashlib.sha256("|".join(descriptions).encode("utf-8")).hexdigest()[:12]


def criterion_stage_map(contract: GoalContract | None) -> dict[str, str]:
    """Criterion name -> stage_id, derived from the task plan (Phase C).

    Criteria not owned by any stage are implicitly terminal.
    """

    mapping: dict[str, str] = {}
    if contract is None or not contract.task_plan:
        return mapping
    for stage in contract.task_plan:
        for name in getattr(stage, "done_criteria", None) or ():
            mapping.setdefault(str(name), stage.stage_id)
    return mapping


def terminal_literal_warnings(contract: GoalContract | None) -> list[dict[str, Any]]:
    """Warn (non-blocking) when a TERMINAL criterion's description embeds a
    full-date or interval literal that may never appear on the final screen.
    """

    if contract is None:
        return []
    staged = set(criterion_stage_map(contract))
    warnings: list[dict[str, Any]] = []
    for criterion in contract.success_criteria:
        if criterion.name in staged:
            continue
        description = str(criterion.description or "")
        literal_kinds: list[str] = []
        if FULL_DATE_LITERAL_RE.search(description):
            literal_kinds.append("full_date_literal")
        if INTERVAL_LITERAL_RE.search(description):
            literal_kinds.append("interval_literal")
        if literal_kinds:
            warnings.append(
                {"criterion": criterion.name, "literal_kinds": literal_kinds}
            )
    return warnings


# ----------------------------------------------------------------------
# L1: screen_text_digest (mechanical evidence, zero model cost)
# ----------------------------------------------------------------------


def _mark_attr(mark: Any, name: str, default: Any = None) -> Any:
    if isinstance(mark, dict):
        return mark.get(name, default)
    return getattr(mark, name, default)


def _ax_mark_texts(marks: Any) -> list[tuple[str, str]]:
    """(mark_id, text) pairs for accessibility-origin marks only.

    LocateAnything / locate_* / la_* marks are excluded by source, with the
    id-prefix check as defense in depth so provider noise never enters L1.
    """

    pairs: list[tuple[str, str]] = []
    for mark in marks or []:
        source = str(_mark_attr(mark, "source") or "")
        if source not in ACCESSIBILITY_MARK_SOURCES:
            continue
        mark_id = str(_mark_attr(mark, "mark_id") or "")
        if not mark_id or mark_id.startswith("locate_") or mark_id.startswith("la_"):
            continue
        text = str(_mark_attr(mark, "text_summary") or _mark_attr(mark, "text") or "")
        text = text.strip()
        if not text:
            continue
        pairs.append((mark_id, text))
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for mark_id, text in pairs:
        if text in seen:
            continue
        seen.add(text)
        deduped.append((mark_id, text))
    return deduped


def screen_text_digest_entry(
    *,
    contract_id: str,
    screen_id: str | None,
    observation_epoch: int | None,
    marks: Any,
    text_limit: int | None = None,
    target_app_entered: bool | None = None,
) -> dict[str, Any] | None:
    """Build one L1 digest entry from accessibility marks (redacted on write).

    Returns None when the screen exposes no accessibility text at all.
    """

    limit = max(1, int(text_limit or l1_digest_text_limit()))
    texts: list[dict[str, str]] = []
    for mark_id, text in _ax_mark_texts(marks)[:limit]:
        redacted = redact_context_text(text)[:L1_DIGEST_TEXT_MAX_CHARS]
        if not redacted:
            continue
        texts.append({"mark_id": str(mark_id)[:64], "text": redacted})
    if not texts:
        return None
    return {
        "kind": "screen_text_digest",
        "contract_id": contract_id,
        "screen_id": screen_id,
        "observation_epoch": observation_epoch,
        "target_app_entered": target_app_entered,
        "count": len(texts),
        "texts": texts,
    }


def append_screen_text_digest(
    existing: list[dict[str, Any]] | None,
    *,
    contract_id: str,
    screen_id: str | None,
    observation_epoch: int | None,
    marks: Any,
    text_limit: int | None = None,
    screen_window: int | None = None,
    target_app_entered: bool | None = None,
) -> list[dict[str, Any]]:
    """Append one L1 digest and return the bounded ledger."""

    entry = screen_text_digest_entry(
        contract_id=contract_id,
        screen_id=screen_id,
        observation_epoch=observation_epoch,
        marks=marks,
        text_limit=text_limit,
        target_app_entered=target_app_entered,
    )
    entries = list(existing or [])
    if entry is not None:
        entries.append(entry)
    return bounded_evidence_ledger(entries, digest_window=screen_window)


# ----------------------------------------------------------------------
# L2: effect_event (reflect tail side effect; never a gate)
# ----------------------------------------------------------------------


def should_record_effect_event(*, verdict: str, hard_failure: bool) -> bool:
    """L2 gate: only succeeded/partial actions whose execution did not hard-
    fail are promoted to effect events. Failed verdicts never record."""

    return verdict in {"succeeded", "partial"} and not hard_failure


def effect_event_entry(
    *,
    contract_id: str,
    action: str | None,
    target: str | None,
    observed_after: str | None,
    screen_id: str | None,
    step: int | None,
    named_evidence: list[dict[str, Any]] | None = None,
    semantic_keys: dict[str, str] | None = None,
    target_app_entered: bool | None = None,
) -> dict[str, Any]:
    """One L2 effect event: mechanical post-action facts + optional reflect
    named_evidence (grounded, redacted) that later folds/judge may trust.
    """

    evidence: list[dict[str, Any]] = []
    for item in named_evidence or []:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "criterion": str(item.get("criterion") or "")[:128],
                "screen_reference": str(item.get("screen_reference") or "")[:128],
                "observed_value": redact_context_text(
                    str(item.get("observed_value") or "")
                )[:200]
                if item.get("observed_value") is not None
                else None,
            }
        )
    return {
        "kind": "effect_event",
        "contract_id": contract_id,
        "action": str(action or "")[:64],
        "target": str(target or "")[:128] if target else None,
        "observed_after": str(observed_after or "")[:64],
        "screen_id": screen_id,
        "step": int(step or 0),
        "target_app_entered": target_app_entered,
        "named_evidence": evidence,
        "semantic_keys": dict(semantic_keys or {}),
    }


def append_effect_event(
    existing: list[dict[str, Any]] | None,
    *,
    contract_id: str,
    action: str | None,
    target: str | None,
    observed_after: str | None,
    screen_id: str | None,
    step: int | None,
    named_evidence: list[dict[str, Any]] | None = None,
    semantic_keys: dict[str, str] | None = None,
    target_app_entered: bool | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Append one L2 effect event; idempotent per (contract, screen, step,
    action) so repeated reflect tails cannot double-record."""

    entries = list(existing or [])
    key = (
        contract_id,
        screen_id,
        int(step or 0),
        str(action or ""),
    )
    for entry in entries:
        if entry.get("kind") != "effect_event":
            continue
        if (
            entry.get("contract_id") == key[0]
            and entry.get("screen_id") == key[1]
            and entry.get("step") == key[2]
            and entry.get("action") == key[3]
        ):
            return entries
    entries.append(
        effect_event_entry(
            contract_id=contract_id,
            action=action,
            target=target,
            observed_after=observed_after,
            screen_id=screen_id,
            step=step,
            named_evidence=named_evidence,
            semantic_keys=semantic_keys,
            target_app_entered=target_app_entered,
        )
    )
    return bounded_evidence_ledger(entries, effect_limit=limit)


# ----------------------------------------------------------------------
# Bounded cross-kind crop
# ----------------------------------------------------------------------


def bounded_evidence_ledger(
    entries: list[dict[str, Any]],
    *,
    criterion_limit: int = 64,
    digest_window: int | None = None,
    effect_limit: int | None = None,
    seal_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Per-kind bounded crop: criterion entries keep the anchor-preserving
    limit, digests keep the last N screens, effect events and seals keep
    their own windows. Per-kind chronological order is preserved, which is
    all the folds require (each kind folds independently).
    """

    if not entries:
        return []
    window = max(1, int(digest_window or l1_digest_screen_window()))
    effect_window = max(1, int(effect_limit or L2_EFFECT_EVENT_LIMIT))
    seal_window = max(1, int(seal_limit or SEAL_LEDGER_LIMIT))
    criterion: list[dict[str, Any]] = []
    digests: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    seals: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for entry in entries:
        kind = entry.get("kind")
        if kind == "screen_text_digest":
            digests.append(entry)
        elif kind == "effect_event":
            effects.append(entry)
        elif kind in {"stage_seal", "stage_unseal"}:
            seals.append(entry)
        elif "criterion_id" in entry:
            criterion.append(entry)
        else:
            other.append(entry)
    bounded_criterion = _bounded_entries(criterion, limit=max(1, criterion_limit))
    return (
        bounded_criterion
        + digests[-window:]
        + effects[-effect_window:]
        + seals[-seal_window:]
        + other
    )


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
    semantic_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.target_app_entered is None:
            value.pop("target_app_entered")
        if self.semantic_key is None:
            value.pop("semantic_key")
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
    semantic_keys: dict[str, str] | None = None,
    limit: int = 64,
) -> list[dict[str, Any]]:
    """Append safe per-criterion results from one Reflect evaluation.

    ``semantic_keys`` (criterion name -> name-independent semantic key) is
    stored per entry so a recompile that renames criteria (same description)
    can inherit the evidence (Stage-Sealing §8).
    """

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
                semantic_key=(
                    (semantic_keys or {}).get(str(criterion_id)) or None
                ),
            ).to_dict()
        )
    return bounded_evidence_ledger(entries, criterion_limit=max(1, limit))


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



def stage_status_from_ledger(
    ledger: list[dict[str, Any]],
    task_plan: tuple[Any, ...] | None,
    *,
    contract_id: str,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fold the evidence ledger into per-stage status (W2 T3).

    Pure ledger fold, zero model calls. A stage is ``satisfied`` when every
    done-criterion is pinned by the ever-matched latch (which is monotonic:
    a matched observation stays latched across transient staleness, and only
    a positive ``contradicted`` counter-observation unlocks). The current
    stage is the first non-satisfied stage in plan order; when every stage is
    satisfied ``current_stage_index`` is None. Returns None when there is no
    plan.

    ``criteria`` (name -> CriterionSpec) is the Stage-Sealing extension:
    when provided, the ledger is remapped by semantic key first, so a
    recompile that renamed criteria (same descriptions) still latches.

    Status is belief/telemetry only — it never gates execution or finish.
    """

    if not task_plan:
        return None
    if criteria:
        ledger = remap_ledger_for_contract(
            ledger, contract_id=contract_id, criteria=criteria, task_plan=task_plan
        )
    per_stage: list[dict[str, Any]] = []
    current_index: int | None = None
    for stage in task_plan:
        satisfied: list[str] = []
        pending: list[str] = []
        for name in stage.done_criteria:
            latch = ever_matched(
                ledger,
                criterion_id=name,
                contract_id=contract_id,
            )
            (satisfied if latch.latched else pending).append(name)
        status = "satisfied" if not pending else "pending"
        per_stage.append(
            {
                "stage_id": getattr(stage, "stage_id", ""),
                "index": getattr(stage, "index", len(per_stage)),
                "status": status,
                "satisfied_criteria": satisfied,
                "pending_criteria": pending,
            }
        )
        if current_index is None and status != "satisfied":
            current_index = getattr(stage, "index", len(per_stage) - 1)
    return {
        "current_stage_index": current_index,
        "per_stage": per_stage,
    }


def criterion_satisfied_by_digest(
    ledger: list[dict[str, Any]],
    *,
    contract_id: str,
    criterion: Any,
) -> tuple[bool, dict[str, Any] | None]:
    """L1 closure: is this raw-text criterion settled by a trusted digest?

    A criterion whose typed predicate expects raw text is mechanically
    satisfied once that expected value was recorded verbatim in any
    ``screen_text_digest`` taken at a target-app-entered observation — even
    if the current (final) screen no longer shows it (Stage-Sealing §3 L1:
    completion is a trajectory property). Only exact matches on digest texts
    close a criterion; everything else stays unknown and is left to the L3
    judge. Returns ``(closed, digest_entry)``.
    """

    predicate = getattr(criterion, "predicate", None)
    if predicate is None:
        return False, None
    definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
    if definition is None or definition.value_domain != "raw_text":
        return False, None
    expected = getattr(predicate, "expected_value", None)
    if expected is None:
        return False, None
    expected_text = normalize_semantic_text(expected)
    if not expected_text:
        return False, None
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") != "screen_text_digest":
            continue
        if entry.get("contract_id") != contract_id:
            continue
        if entry.get("target_app_entered") is not True:
            continue
        for item in entry.get("texts") or []:
            if normalize_semantic_text(item.get("text")) == expected_text:
                return True, entry
    return False, None


# ----------------------------------------------------------------------
# Stage Sealing (Phase B): seal records, authority resolution, revocation
# ----------------------------------------------------------------------


def _stage_seal_key(entry: dict[str, Any]) -> str:
    return str(entry.get("semantic_key") or "")


def _criteria_map(contract: GoalContract | None) -> dict[str, Any]:
    if contract is None:
        return {}
    return {criterion.name: criterion for criterion in contract.success_criteria}


def seal_records_for_contract(
    ledger: list[dict[str, Any]],
    *,
    contract: GoalContract | None,
    contract_id: str,
) -> list[dict[str, Any]]:
    """Resolve the ACTIVE stage seals against the current contract.

    A seal's semantic key is name-independent; it is resolved through the
    current task plan so renamed-but-semantically-identical stages keep their
    authority (Stage-Sealing §4.2/§8). An unseal entry for the same key
    revokes the seal; a later seal re-arms it.
    """

    if contract is None or not contract.task_plan:
        return []
    criteria = _criteria_map(contract)
    key_to_stage = {
        stage_semantic_key(stage, criteria): stage for stage in contract.task_plan
    }
    active: dict[str, dict[str, Any]] = {}
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("contract_id") != contract_id:
            continue
        kind = entry.get("kind")
        if kind == "stage_seal":
            stage = key_to_stage.get(_stage_seal_key(entry))
            if stage is not None:
                active[entry["semantic_key"]] = {
                    "stage_id": stage.stage_id,
                    "semantic_key": entry["semantic_key"],
                    "criteria_sealed": list(stage.done_criteria),
                    "evidence_refs": list(entry.get("evidence_refs") or []),
                    "screen_id": entry.get("screen_id"),
                    "step": entry.get("step"),
                    "sealed_at": entry.get("sealed_at"),
                }
        elif kind == "stage_unseal":
            active.pop(entry.get("semantic_key"), None)
    return list(active.values())


def sealed_criteria(
    ledger: list[dict[str, Any]],
    *,
    contract: GoalContract | None,
    contract_id: str,
) -> set[str]:
    """Criterion names currently sealed (authoritative) for this contract."""

    sealed: set[str] = set()
    for record in seal_records_for_contract(ledger, contract=contract, contract_id=contract_id):
        sealed.update(record["criteria_sealed"])
    return sealed


def seal_satisfied_stages(
    ledger: list[dict[str, Any]],
    *,
    contract: GoalContract | None,
    contract_id: str,
    screen_id: str | None,
    step: int | None,
    evidence_refs: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Eagerly seal every stage whose done criteria are all latched.

    Idempotent: a stage already sealed (same semantic key, still active) is
    skipped. Returns ``(ledger, new_seals)`` where ``new_seals`` lists only
    the records actually written now.
    """

    if contract is None or not contract.task_plan:
        return list(ledger or []), []
    criteria = _criteria_map(contract)
    folded = stage_status_from_ledger(
        ledger,
        contract.task_plan,
        contract_id=contract_id,
        criteria=criteria,
    )
    if folded is None:
        return list(ledger or []), []
    per_stage = {item["stage_id"]: item for item in folded["per_stage"]}
    active_keys = {
        record["semantic_key"]
        for record in seal_records_for_contract(
            ledger, contract=contract, contract_id=contract_id
        )
    }
    new_ledger = list(ledger or [])
    new_seals: list[dict[str, Any]] = []
    for stage in contract.task_plan:
        item = per_stage.get(stage.stage_id)
        if item is None or item["status"] != "satisfied":
            continue
        key = stage_semantic_key(stage, criteria)
        if key in active_keys:
            continue
        record = {
            "kind": "stage_seal",
            "contract_id": contract_id,
            "stage_id": stage.stage_id,
            "criteria_sealed": list(stage.done_criteria),
            "evidence_refs": list(evidence_refs or []),
            "screen_id": screen_id,
            "step": int(step or 0),
            "sealed_at": int(step or 0),
            "semantic_key": key,
        }
        new_ledger.append(record)
        new_seals.append(record)
        active_keys.add(key)
    return bounded_evidence_ledger(new_ledger), new_seals


def revoke_seals_on_contradiction(
    ledger: list[dict[str, Any]],
    *,
    contract: GoalContract | None,
    contract_id: str,
    contradicted_criteria: set[str],
    screen_id: str | None = None,
    step: int | None = None,
) -> list[dict[str, Any]]:
    """Write ``stage_unseal`` entries for every active seal covering a
    criterion with a POSITIVE counter-observation (P0 #13a).

    Existential absence (``unknown``) never revokes a seal — only an actual
    contradiction does. A revoked stage returns to open and may re-seal later
    once its criteria latch again.
    """

    if not contradicted_criteria:
        return list(ledger or [])
    active = seal_records_for_contract(ledger, contract=contract, contract_id=contract_id)
    touched: set[str] = set()
    for record in active:
        hit = [name for name in record["criteria_sealed"] if name in contradicted_criteria]
        if hit:
            touched.add(record["semantic_key"])
    if not touched:
        return list(ledger or [])
    new_ledger = list(ledger or [])
    for record in active:
        if record["semantic_key"] not in touched:
            continue
        new_ledger.append(
            {
                "kind": "stage_unseal",
                "contract_id": contract_id,
                "stage_id": record["stage_id"],
                "semantic_key": record["semantic_key"],
                "criteria": [
                    name
                    for name in record["criteria_sealed"]
                    if name in contradicted_criteria
                ],
                "screen_id": screen_id,
                "step": int(step or 0),
                "reason": "positive_counter_observation",
            }
        )
    return bounded_evidence_ledger(new_ledger)


# ----------------------------------------------------------------------
# Recompile inheritance (Stage-Sealing §8): semantic-key remap
# ----------------------------------------------------------------------


def remap_ledger_for_contract(
    ledger: list[dict[str, Any]],
    *,
    contract_id: str,
    criteria: dict[str, Any],
    task_plan: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Remap criterion references by semantic key onto the current contract.

    * criterion evidence entries whose stored ``semantic_key`` matches a
      current criterion's key are renamed to the current criterion name;
    * ``stage_seal`` entries whose key matches a current stage's key are
      remapped to the current stage's done-criterion names;
    * ``effect_event`` named_evidence names are remapped through their stored
      ``semantic_keys`` map.

    Entries whose key matches nothing are kept as-is (folds filter by name),
    so a partially divergent recompile degrades fail-closed instead of
    resurrecting stale evidence.
    """

    if not ledger:
        return []
    key_to_name = {
        criterion_semantic_key(_criterion_description(criterion)): name
        for name, criterion in criteria.items()
    }
    key_to_stage = {
        stage_semantic_key(stage, criteria): stage for stage in (task_plan or ())
    }
    remapped: list[dict[str, Any]] = []
    for entry in ledger:
        if not isinstance(entry, dict):
            remapped.append(entry)
            continue
        if entry.get("contract_id") != contract_id:
            remapped.append(entry)
            continue
        kind = entry.get("kind")
        if kind == "stage_seal":
            new = dict(entry)
            stage = key_to_stage.get(_stage_seal_key(entry))
            if stage is not None:
                new["criteria_sealed"] = list(stage.done_criteria)
            remapped.append(new)
        elif kind == "effect_event":
            new = dict(entry)
            keys = entry.get("semantic_keys") or {}
            named: list[dict[str, Any]] = []
            for item in entry.get("named_evidence") or []:
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                old_name = str(item.get("criterion") or "")
                if old_name in keys:
                    target = key_to_name.get(keys[old_name])
                    if target:
                        item["criterion"] = target
                named.append(item)
            new["named_evidence"] = named
            remapped.append(new)
        elif "criterion_id" in entry:
            new = dict(entry)
            stored_key = entry.get("semantic_key")
            if stored_key and stored_key in key_to_name:
                new["criterion_id"] = key_to_name[stored_key]
            remapped.append(new)
        else:
            remapped.append(entry)
    return remapped
