"""GoalEvaluator: validates finish claims against GoalContract criteria.

Replaces the keyword-based ``validate_finish_claim``.  For each
``SuccessCriterion`` in the contract, checks the criterion using the
appropriate signal source:

* ``accessibility_text_match`` → raw-text contains matching, with legacy sha256 support
* ``object_hash_match`` → verifier_evidence.selected_object_match
* ``object_rank_match`` → verifier_evidence.selected_object_expected_rank == ordinal
* ``app_or_activity_match`` → current_app / top_activity package match
* ``focus_or_keyboard`` → verifier focus signals
* ``vlm_judge`` → three-part check: (1) named in finish_claim_matched,
  (2) grounded screen_reference in reflect_named_evidence,
  (3) not contradicted by a programmatic criterion's missing signal
* ``external_probe`` → callable from configurable["goal_probes"]

Fail-closed: ``unknown`` (no explicit missing, but not all required matched)
does NOT auto-upgrade to ``success``.  Programmatic contradiction overrides
vlm_judge self-attestation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal, Protocol

from phone_agent.graph.goal import (
    LEGACY_SHA256_STUB_PATTERN,
    GoalContract,
    SuccessCriterion,
)
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    EvidenceReference,
    Matcher,
    ObservedFact,
)

_PLACEHOLDER_SCREEN_REFERENCES = frozenset(
    {
        "screen",
        "current_screen",
        "unknown",
        "none",
        "n/a",
        "null",
        "屏幕",
        "当前屏幕",
        "全屏",
    }
)

# Format-drift separator normalization: whitespace, hyphens, and underscores
# all collapse to a single underscore. This is deliberately cosmetic — it maps
# "Flight Search Parameters" / "flight-search-parameters" onto
# "flight_search_parameters" without any synonym or substring semantics.
_CRITERION_NAME_SEPARATOR_RE = re.compile(r"[\s\-_]+")


def _normalize_criterion_name(value: Any) -> str:
    """Casefold + collapse whitespace/hyphen/underscore into one underscore.

    Repairs presentation drift only (case, spacing, separators). It never
    matches by meaning: a name that does not normalize onto a contract
    criterion stays missing (fail-closed), and empty results are dropped.
    """
    text = str(value or "").casefold().strip()
    return _CRITERION_NAME_SEPARATOR_RE.sub("_", text).strip("_")


# Only raw on-screen text needs a judgement call: whether a label *means* the
# goal was reached is a semantic question. Identifiers, digests, and scalars
# (foreground app, rank, toggle state, focus) are structural facts the system
# reads exactly, so asking the model to echo them adds a guess and no
# information. The axis is the value domain, not the evidence source: an
# accessibility node yields both raw text and booleans.
_JUDGEMENT_VALUE_DOMAINS: frozenset[str] = frozenset({"raw_text"})


def _is_self_observable(criterion: SuccessCriterion) -> bool:
    """Whether the system can settle this criterion without model testimony."""

    if criterion.verification == "vlm_judge":
        return False
    predicate = criterion.predicate
    if predicate is None:
        # No typed predicate: the verification kind alone decides, and every
        # non-vlm_judge kind has a programmatic check.
        return True
    definition = CORE_PREDICATE_CATALOG.get(predicate.predicate_id)
    return definition.value_domain not in _JUDGEMENT_VALUE_DOMAINS


def _checkable_states(observation: Any) -> set[bool]:
    """Collect checked states of visible checkable nodes in an observation payload."""

    if not isinstance(observation, dict):
        return set()
    states: set[bool] = set()
    structures = observation.get("screen_structures")
    for structure in structures if isinstance(structures, list) else []:
        if not isinstance(structure, dict):
            continue
        nodes = structure.get("nodes")
        for node in (nodes.values() if isinstance(nodes, dict) else []):
            if not isinstance(node, dict) or not node.get("checkable"):
                continue
            if node.get("visible") is False:
                continue
            states.add(bool(node.get("checked")))
    return states


def _is_placeholder_screen_reference(value: str) -> bool:
    """Reject vlm_judge screen references with no discriminating information.

    A grounded reference must identify a concrete UI element or region
    (mark id, text snippet, object id...). Bare placeholders like
    "region-1" or "screen" carry no verifiable content.
    """
    import re as _re

    normalized = value.strip().casefold()
    if normalized in _PLACEHOLDER_SCREEN_REFERENCES:
        return True
    if _re.fullmatch(r"(region|area|zone|box|区域|地区)[-_ ]?\d*", normalized):
        return True
    return False


# ----------------------------------------------------------------------
# Result
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class GoalEvaluation:
    status: Literal["success", "failure", "unknown"]
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    soft_matched: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "matched_terminal_evidence": list(self.matched),
            "missing_terminal_evidence": list(self.missing),
            "soft_matched": list(self.soft_matched),
            "evidence": dict(self.evidence),
        }


# ----------------------------------------------------------------------
# Protocol
# ----------------------------------------------------------------------


class GoalEvaluator(Protocol):
    def evaluate(
        self,
        *,
        contract: GoalContract,
        verifier_status: str | None,
        verifier_evidence: dict[str, Any] | None,
        after_observation: dict[str, Any] | None,
        device_signals: dict[str, Any] | None,
        finish_claim_matched: list[str],
        reflect_named_evidence: list[dict[str, Any]] | None,
        trajectory_summary: str | None = None,
        goal_probes: dict[str, Any] | None = None,
    ) -> GoalEvaluation: ...


class PureGoalEvaluator:
    """Pure typed-criterion fold over a bounded, already-matched evidence ledger.

    Stage-Sealing (Phase B): the ledger is first remapped by semantic key onto
    the current contract (recompile inheritance), and criteria sealed by an
    active ``stage_seal`` record are authoritative (``matched``) without any
    re-verification or current-observation freshness requirement.
    """

    _BLOCKING = {"invalid", "contradicted", "stale", "missing"}

    def evaluate(
        self,
        *,
        contract: GoalContract,
        contract_id: str,
        evidence_ledger: list[dict[str, Any]],
        finish_claim_matched: list[str],
        screen_id: str,
        observation_epoch: int,
    ) -> GoalEvaluation:
        from phone_agent.graph.goal_evidence import (
            remap_ledger_for_contract,
            sealed_criteria,
        )

        ledger = remap_ledger_for_contract(
            evidence_ledger,
            contract_id=contract_id,
            criteria={item.name: item for item in contract.success_criteria},
            task_plan=contract.task_plan,
        )
        sealed = sealed_criteria(
            ledger, contract=contract, contract_id=contract_id
        )
        claim_ids = set(finish_claim_matched)
        criterion_ids = {criterion.name for criterion in contract.success_criteria}
        unknown_claim_ids = sorted(claim_ids - criterion_ids)
        latest: dict[str, dict[str, Any]] = {}
        for entry in ledger:
            if not isinstance(entry, dict):
                continue
            if entry.get("contract_id") != contract_id:
                continue
            criterion_id = str(entry.get("criterion_id") or "")
            if criterion_id:
                latest[criterion_id] = entry

        matched: list[str] = []
        missing: list[str] = []
        unknown: list[str] = []
        results: dict[str, dict[str, Any]] = {}
        for criterion in contract.success_criteria:
            if criterion.name in sealed:
                results[criterion.name] = {
                    "status": "matched",
                    "reason": "sealed_by_stage",
                    "predicate_id": (
                        criterion.predicate.predicate_id
                        if criterion.predicate is not None
                        else None
                    ),
                }
                matched.append(criterion.name)
                continue
            if criterion.name not in claim_ids:
                status = "missing"
                reason = "not_named_in_finish_claim"
            elif criterion.predicate is None:
                status = "invalid"
                reason = "typed_predicate_missing"
            else:
                entry = latest.get(criterion.name)
                if entry is None:
                    status = "unobserved"
                    reason = "criterion_unobserved"
                elif criterion.freshness == "current_observation" and (
                    entry.get("screen_id") != screen_id
                    or entry.get("observation_epoch") != observation_epoch
                ):
                    status = "stale"
                    reason = "evidence_binding_stale"
                else:
                    status = str(entry.get("status") or "unknown")
                    reason = str(entry.get("reason_code") or "unspecified")
            results[criterion.name] = {
                "status": status,
                "reason": reason,
                "predicate_id": (
                    criterion.predicate.predicate_id
                    if criterion.predicate is not None
                    else None
                ),
            }
            if status == "matched":
                matched.append(criterion.name)
            elif status in self._BLOCKING:
                missing.append(criterion.name)
            else:
                unknown.append(criterion.name)

        required = {item.name for item in contract.success_criteria if item.required}
        if unknown_claim_ids:
            overall = "failure"
        elif required.intersection(missing):
            overall: Literal["success", "failure", "unknown"] = "failure"
        elif required and required.issubset(matched):
            overall = "success"
        else:
            overall = "unknown"
        return GoalEvaluation(
            status=overall,
            matched=matched,
            missing=missing,
            soft_matched=unknown,
            evidence={
                "per_criterion": results,
                "goal_type": "typed_contract",
                "finish_claim_matched": sorted(claim_ids),
                "unknown_finish_claim_ids": unknown_claim_ids,
                "screen_id": screen_id,
                "observation_epoch": observation_epoch,
            },
        )


# ----------------------------------------------------------------------
# AggregatingGoalEvaluator
# ----------------------------------------------------------------------


class AggregatingGoalEvaluator:
    """Evaluate each criterion, aggregate, and apply fail-closed semantics."""

    def evaluate(
        self,
        *,
        contract: GoalContract,
        verifier_status: str | None = None,
        verifier_evidence: dict[str, Any] | None = None,
        after_observation: dict[str, Any] | None = None,
        device_signals: dict[str, Any] | None = None,
        finish_claim_matched: list[str] | None = None,
        reflect_named_evidence: list[dict[str, Any]] | None = None,
        trajectory_summary: str | None = None,
        goal_probes: dict[str, Any] | None = None,
    ) -> GoalEvaluation:
        finish_matched_set = set(finish_claim_matched or [])
        contract_normalized = {
            _normalize_criterion_name(crit.name) for crit in contract.success_criteria
        }
        named_evidence_map: dict[str, dict[str, Any]] = {}
        ignored_evidence_names: list[str] = []
        for item in reflect_named_evidence or []:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("criterion", ""))
            normalized = _normalize_criterion_name(raw_name)
            if not normalized:
                continue
            if normalized not in contract_normalized:
                # A judge name outside the contract whitelist must never
                # satisfy a criterion — record it for trace diagnosis only.
                ignored_evidence_names.append(raw_name)
                continue
            named_evidence_map.setdefault(normalized, item)
        # Distinguish "VLM not yet consulted" (None) from "VLM ran but no evidence" (empty list)
        vlm_not_run = reflect_named_evidence is None

        matched: list[str] = []
        missing: list[str] = []
        soft_matched: list[str] = []
        per_criterion: dict[str, dict[str, Any]] = {}

        # First pass: check each criterion
        programmatic_missing: set[str] = set()
        for crit in contract.success_criteria:
            result = self._check_criterion(
                crit,
                contract=contract,
                verifier_status=verifier_status,
                verifier_evidence=verifier_evidence,
                after_observation=after_observation,
                device_signals=device_signals,
                finish_matched_set=finish_matched_set,
                named_evidence_map=named_evidence_map,
                vlm_not_run=vlm_not_run,
                goal_probes=goal_probes,
            )
            per_criterion[crit.name] = result
            status = result["status"]
            if status == "matched":
                matched.append(crit.name)
            elif status == "missing":
                missing.append(crit.name)
                if crit.verification != "vlm_judge" and crit.required:
                    programmatic_missing.add(crit.name)
            elif status in {"contradicted", "invalid", "stale"}:
                missing.append(crit.name)
                if crit.required:
                    programmatic_missing.add(crit.name)
            elif status == "unknown":
                soft_matched.append(crit.name)
            elif status == "soft_matched":
                soft_matched.append(crit.name)

        # Second pass: vlm_judge override — programmatic contradiction wins
        final_missing = list(missing)
        for crit in contract.success_criteria:
            if crit.verification != "vlm_judge":
                continue
            if crit.name in matched and crit.name in programmatic_missing:
                # A programmatic criterion contradicts this vlm_judge claim
                matched.remove(crit.name)
                if crit.name not in final_missing:
                    final_missing.append(crit.name)
                per_criterion[crit.name]["status"] = "missing"
                per_criterion[crit.name][
                    "override_reason"
                ] = "programmatic_contradiction"

        # Determine status
        required_names = {c.name for c in contract.success_criteria if c.required}
        required_matched = {
            c.name
            for c in contract.success_criteria
            if c.required and c.name in matched
        }
        required_missing = {
            c.name
            for c in contract.success_criteria
            if c.required and c.name in final_missing
        }

        if required_missing:
            status: Literal["success", "failure", "unknown"] = "failure"
        elif required_names and required_matched == required_names:
            status = "success"
        elif not required_names:
            status = "unknown"
        else:
            # No explicit missing but not all required matched → unknown (soft/unverified)
            status = "unknown"

        evidence: dict[str, Any] = {
            "per_criterion": per_criterion,
            "goal_type": "declarative_contract",
            "ordinal": contract.ordinal,
            "verification_strategy": contract.verification_strategy,
            "finish_claim_matched": sorted(finish_matched_set),
        }
        if ignored_evidence_names:
            evidence["named_evidence_ignored"] = sorted(
                {name for name in ignored_evidence_names if name}
            )
        return GoalEvaluation(
            status=status,
            matched=matched,
            missing=final_missing,
            soft_matched=soft_matched,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Per-criterion dispatch
    # ------------------------------------------------------------------

    def _check_criterion(
        self,
        crit: SuccessCriterion,
        *,
        contract: GoalContract,
        verifier_status: str | None,
        verifier_evidence: dict[str, Any] | None,
        after_observation: dict[str, Any] | None,
        device_signals: dict[str, Any] | None,
        finish_matched_set: set[str],
        named_evidence_map: dict[str, dict[str, Any]],
        vlm_not_run: bool,
        goal_probes: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Facts the system can read for itself are never delegated to the
        # model. Asking it to echo a canonical app id or a rank made finishing
        # depend on guessing a value it was never shown, while the ground truth
        # sat unread in the snapshot / object registry / verifier signals.
        if _is_self_observable(crit):
            programmatic = self._check_programmatic(
                crit,
                contract=contract,
                verifier_evidence=verifier_evidence,
                after_observation=after_observation,
                device_signals=device_signals,
                goal_probes=goal_probes,
            )
            if programmatic is not None:
                # Deliberately not gated on finish_matched_set: naming exists to
                # stop the model claiming criteria it cannot see. Where the
                # system reads ground truth, the model's endorsement is
                # irrelevant and requiring it would recreate the dependency this
                # dispatch removes.
                return programmatic
        if crit.predicate is not None:
            typed = self._check_typed_predicate(
                crit,
                contract=contract,
                finish_matched_set=finish_matched_set,
                named_evidence_map=named_evidence_map,
                vlm_not_run=vlm_not_run,
            )
            if crit.verification != "vlm_judge":
                return typed
            # vlm_judge criterion with an attached predicate: the typed check
            # is corroborating evidence in the same value domain as the
            # provider output. A match upgrades directly; a contradiction is
            # genuine counter-evidence and vetoes. Only an unobservable typed
            # fact falls back to the vlm_judge self-attestation path.
            if typed.get("status") == "matched":
                return typed
            if typed.get("status") == "contradicted":
                return {
                    "status": "missing",
                    "reason": "typed_contradiction",
                    **{
                        key: value
                        for key, value in typed.items()
                        if key not in {"status", "reason"}
                    },
                }
            return self._check_vlm_judge(
                crit, finish_matched_set, named_evidence_map, vlm_not_run
            )
        programmatic = self._check_programmatic(
            crit,
            contract=contract,
            verifier_evidence=verifier_evidence,
            after_observation=after_observation,
            device_signals=device_signals,
            goal_probes=goal_probes,
        )
        if programmatic is not None:
            return programmatic
        if crit.verification == "vlm_judge":
            return self._check_vlm_judge(
                crit, finish_matched_set, named_evidence_map, vlm_not_run
            )
        return {"status": "missing", "reason": "unknown_verification"}

    def _check_programmatic(
        self,
        crit: SuccessCriterion,
        *,
        contract: GoalContract,
        verifier_evidence: dict[str, Any] | None,
        after_observation: dict[str, Any] | None,
        device_signals: dict[str, Any] | None,
        goal_probes: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Evaluate a criterion from device/observation truth, or None if it has
        no programmatic check."""

        if crit.verification == "accessibility_text_match":
            return self._check_accessibility_text(crit, after_observation)
        if crit.verification == "object_hash_match":
            return self._check_object_hash(crit, verifier_evidence)
        if crit.verification == "object_rank_match":
            return self._check_object_rank(crit, contract, verifier_evidence)
        if crit.verification == "app_or_activity_match":
            return self._check_app_or_activity(
                crit, contract, after_observation, device_signals
            )
        if crit.verification == "focus_or_keyboard":
            return self._check_focus_or_keyboard(
                crit, after_observation, device_signals
            )
        if crit.verification == "toggle_state_match":
            return self._check_toggle_state(crit, after_observation)
        if crit.verification == "external_probe":
            return self._check_external_probe(crit, goal_probes)
        return None

    def _check_typed_predicate(
        self,
        crit: SuccessCriterion,
        *,
        contract: GoalContract,
        finish_matched_set: set[str],
        named_evidence_map: dict[str, dict[str, Any]],
        vlm_not_run: bool,
    ) -> dict[str, Any]:
        """Match a typed expected predicate against current grounded evidence."""

        if crit.name not in finish_matched_set:
            return {"status": "missing", "reason": "not_named_in_finish_claim"}
        if vlm_not_run:
            return {"status": "unknown", "reason": "typed_fact_not_yet_collected"}
        evidence = named_evidence_map.get(_normalize_criterion_name(crit.name))
        if not isinstance(evidence, dict):
            return {"status": "missing", "reason": "typed_fact_missing"}
        screen_reference = str(evidence.get("screen_reference") or "").strip()
        if not screen_reference:
            return {"status": "missing", "reason": "no_screen_reference"}
        # An ungrounded reference cannot be audited back to a concrete element,
        # so the reported value carries no verifiable provenance — reject it
        # even when the value itself would match.
        if _is_placeholder_screen_reference(screen_reference):
            return {"status": "missing", "reason": "placeholder_screen_reference"}
        observed_value = evidence.get("observed_value")
        source = str(evidence.get("source") or "visual_region")
        if source not in {
            "accessibility",
            "screen_object",
            "mark",
            "visual_region",
            "whole_screen",
            "external_probe",
            "device",
        }:
            return {"status": "invalid", "reason": "fact_source_invalid"}
        screen_id = str(evidence.get("screen_id") or "current_screen")
        epoch_value = evidence.get("observation_epoch", 0)
        epoch = epoch_value if isinstance(epoch_value, int) else 0
        try:
            reference = EvidenceReference(
                source_kind=source,  # type: ignore[arg-type]
                reference_id=screen_reference,
                screen_id=screen_id,
                observation_epoch=epoch,
            )
            fact = ObservedFact(
                predicate_id=crit.predicate.predicate_id,
                observed_value=observed_value,
                confidence=float(evidence.get("confidence", 1.0)),
                source=source,  # type: ignore[arg-type]
                evidence_reference=reference,
                contract_id=contract.task_hash,
                screen_id=screen_id,
                observation_epoch=epoch,
                provider_version=str(evidence.get("provider_version") or "reflect_v1"),
            )
            CORE_PREDICATE_CATALOG.validate_fact(fact)
        except (TypeError, ValueError):
            return {"status": "invalid", "reason": "typed_fact_invalid"}
        result = Matcher.match(crit.predicate, fact)
        return {
            "status": result.status,
            "reason": (
                "target_mismatch"
                if result.status == "contradicted"
                else result.reason_code
            ),
            "source": source,
            "confidence_bucket": (
                "high"
                if fact.confidence >= 0.9
                else "medium" if fact.confidence >= 0.6 else "low"
            ),
        }

    # ------------------------------------------------------------------
    # Programmatic checks (reuse verifier.py primitives)
    # ------------------------------------------------------------------

    def _check_accessibility_text(
        self, crit: SuccessCriterion, after_observation: dict[str, Any] | None
    ) -> dict[str, Any]:
        from phone_agent.graph.verifier import _match_expected_text, _observation_text

        digest_list = self._extract_sha256_stubs(crit.description)
        text_blob = _observation_text(after_observation)
        if digest_list:
            expected = [f"sha256:{digest}" for digest in digest_list]
        else:
            expected_text = crit.description.strip().strip("\"'` “”‘’「」『』")
            expected = [expected_text] if expected_text else []
        matched, missing = _match_expected_text(expected, text_blob)
        if missing:
            return {
                "status": "unknown",
                "reason": "not_observed_in_view",
                "missing": missing,
            }
        if not matched:
            return {"status": "unknown", "reason": "not_observed_in_view"}
        return {"status": "matched", "reason": "text_matched", "matched": matched}

    def _check_object_hash(
        self, crit: SuccessCriterion, verifier_evidence: dict[str, Any] | None
    ) -> dict[str, Any]:
        signals = (verifier_evidence or {}).get("selected_object_signals") or {}
        if signals.get("selected_object_match"):
            return {"status": "matched", "reason": "object_hash_match"}
        if signals.get("wrong_detail_opened") or signals.get(
            "same_surface_still_visible"
        ):
            return {"status": "missing", "reason": "wrong_object_or_surface"}
        return {"status": "unknown", "reason": "no_object_hash_signal"}

    def _check_object_rank(
        self,
        crit: SuccessCriterion,
        contract: GoalContract,
        verifier_evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if contract.ordinal is None:
            return {"status": "missing", "reason": "no_ordinal_in_contract"}
        signals = (verifier_evidence or {}).get("selected_object_signals") or {}
        expected_rank = signals.get("selected_object_expected_rank")
        if signals.get("selected_object_match") and expected_rank == contract.ordinal:
            return {"status": "matched", "reason": f"rank_{contract.ordinal}_match"}
        # Weakest admissible evidence, kept only because the verifier cannot
        # always identify the selected object's content hash (selected_object_match
        # is None when expected_outcome was not pre-declared). The strong signal is
        # ui.object_rank from ObjectFactProvider, which PureGoalEvaluator folds in
        # and which overrides this; `soft` marks the distinction in the trace so a
        # run resting on this alone is visible rather than indistinguishable from
        # a positive rank confirmation.
        if (
            expected_rank is None
            and signals.get("selected_object_detail_signal")
            and not signals.get("wrong_detail_opened")
            and not signals.get("same_surface_still_visible")
        ):
            return {
                "status": "matched",
                "reason": f"rank_{contract.ordinal}_detail_only_soft_match",
                "soft": True,
            }
        return {
            "status": "missing",
            "reason": f"rank_{contract.ordinal}_not_matched",
            "expected_rank": contract.ordinal,
            "actual_rank": expected_rank,
        }

    def _check_app_or_activity(
        self,
        crit: SuccessCriterion,
        contract: GoalContract,
        after_observation: dict[str, Any] | None,
        device_signals: dict[str, Any] | None,
    ) -> dict[str, Any]:
        from phone_agent.graph.verifier import _package_for_app_name

        app_hint = (contract.target_app_hint or "").lower()
        if not app_hint:
            return {"status": "missing", "reason": "no_app_hint"}

        current_app = self._current_app(after_observation).lower()
        top_activity = str((device_signals or {}).get("top_activity") or "").lower()

        target_pkg = _package_for_app_name(contract.target_app_hint or "") or ""
        if target_pkg and (target_pkg in current_app or target_pkg in top_activity):
            return {"status": "matched", "reason": "package_match"}
        if app_hint in current_app or app_hint in top_activity:
            return {"status": "matched", "reason": "app_hint_match"}
        return {
            "status": "missing",
            "reason": "app_not_in_foreground",
            "current_app": current_app,
        }

    def _check_focus_or_keyboard(
        self,
        crit: SuccessCriterion,
        after_observation: dict[str, Any] | None,
        device_signals: dict[str, Any] | None,
    ) -> dict[str, Any]:
        from phone_agent.graph.verifier import _focus_signals

        focus = _focus_signals(after_observation)
        keyboard = (device_signals or {}).get("keyboard_visible")
        if focus.get("focused_editable") or focus.get("keyboard_visible") or keyboard:
            return {"status": "matched", "reason": "focus_or_keyboard_visible"}
        return {"status": "missing", "reason": "no_focus_or_keyboard"}

    def _check_toggle_state(
        self,
        crit: SuccessCriterion,
        after_observation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Match a toggle criterion against checkable accessibility nodes.

        The authoritative signal is ``ui.toggle_state`` collected by
        ``AccessibilityFactProvider`` through the typed-predicate path. This
        text-payload check only covers contracts whose criterion carries no
        predicate, and reports ``unknown`` (never a false negative) when the
        observation payload has no checkable node to read.
        """
        expected = crit.predicate.expected_value if crit.predicate else None
        states = _checkable_states(after_observation)
        if not states:
            return {"status": "unknown", "reason": "no_toggle_signal"}
        if expected is None:
            return {"status": "unknown", "reason": "no_expected_toggle_state"}
        if expected in states:
            return {"status": "matched", "reason": "toggle_state_match"}
        return {"status": "contradicted", "reason": "toggle_state_mismatch"}

    def _check_external_probe(
        self, crit: SuccessCriterion, goal_probes: dict[str, Any] | None
    ) -> dict[str, Any]:
        probe_id = crit.probe_id or crit.name
        probes = goal_probes or {}
        probe = probes.get(probe_id)
        if probe is None:
            return {"status": "missing", "reason": "probe_not_registered"}
        try:
            result = probe() if callable(probe) else bool(probe)
        except Exception as exc:
            return {"status": "missing", "reason": "probe_error", "error": str(exc)}
        if result:
            return {"status": "matched", "reason": "probe_passed"}
        return {"status": "missing", "reason": "probe_failed"}

    # ------------------------------------------------------------------
    # vlm_judge — three-part check
    # ------------------------------------------------------------------

    def _check_vlm_judge(
        self,
        crit: SuccessCriterion,
        finish_matched_set: set[str],
        named_evidence_map: dict[str, dict[str, Any]],
        vlm_not_run: bool,
    ) -> dict[str, Any]:
        # Part 1: criterion must be named in finish.matched_terminal_evidence
        if crit.name not in finish_matched_set:
            return {"status": "missing", "reason": "not_named_in_finish_claim"}

        # Part 2: must have a grounded screen_reference in reflect_named_evidence
        # When VLM has not been consulted yet (named_evidence_map is empty), emit "unknown"
        # so the caller knows to run the VLM for evidence. This is fail-closed: unknown
        # does NOT auto-upgrade to success.
        if vlm_not_run:
            return {"status": "unknown", "reason": "vlm_not_yet_consulted"}
        evidence = named_evidence_map.get(_normalize_criterion_name(crit.name))
        if not evidence or not isinstance(evidence, dict):
            return {"status": "missing", "reason": "no_named_evidence_from_reflect"}
        screen_ref = str(evidence.get("screen_reference") or "").strip()
        if not screen_ref:
            return {"status": "missing", "reason": "no_screen_reference"}
        if _is_placeholder_screen_reference(screen_ref):
            return {"status": "missing", "reason": "placeholder_screen_reference"}

        # Part 3: programmatic contradiction is handled in the second pass
        return {
            "status": "matched",
            "reason": "vlm_judge_with_grounded_evidence",
            "screen_reference": screen_ref,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sha256_stubs(text: str) -> list[str]:
        return LEGACY_SHA256_STUB_PATTERN.findall(text)

    @staticmethod
    def _current_app(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        snapshot = value.get("snapshot")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("current_app"), str):
            return snapshot["current_app"]
        if isinstance(value.get("current_app"), str):
            return value["current_app"]
        return ""


# Module-level singleton for convenience
default_goal_evaluator = AggregatingGoalEvaluator()
pure_goal_evaluator = PureGoalEvaluator()


def resolve_programmatic_criteria(
    *,
    goal_contract: GoalContract,
    verifier_evidence: dict[str, Any] | None,
    after_observation: dict[str, Any] | None,
    device_signals: dict[str, Any] | None,
    goal_probes: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Resolve self-observable, predicate-less criteria from verifier signals.

    Stage-Sealing acceptance (Phase C): typed criteria are settled by the fact
    providers from the ledger, and judge criteria wait for the L3 judge. But a
    self-observable criterion that carries no typed predicate (legacy
    ``object_rank_match`` / ``object_hash_match`` / ``app_or_activity_match`` /
    ``focus_or_keyboard`` / ``accessibility_text_match`` contracts) has no
    provider fact at all — the only mechanical truth available is the current
    observation's verifier signals, so they are resolved here and written into
    the ledger for the acceptance fold (mechanical evidence outranks model
    testimony; absence maps to ``missing`` and never upgrades).
    """
    resolved: dict[str, dict[str, Any]] = {}
    if goal_contract is None:
        return resolved
    for criterion in goal_contract.success_criteria:
        if criterion.predicate is not None:
            continue
        if not _is_self_observable(criterion):
            continue
        result = default_goal_evaluator._check_programmatic(
            criterion,
            contract=goal_contract,
            verifier_evidence=verifier_evidence,
            after_observation=after_observation,
            device_signals=device_signals,
            goal_probes=goal_probes,
        )
        if result is None:
            continue
        resolved[criterion.name] = result
    return resolved


def evaluate_finish_claim(
    *,
    contract: GoalContract,
    verifier_status: str | None = None,
    verifier_evidence: dict[str, Any] | None = None,
    after_observation: dict[str, Any] | None = None,
    device_signals: dict[str, Any] | None = None,
    finish_claim_matched: list[str] | None = None,
    reflect_named_evidence: list[dict[str, Any]] | None = None,
    goal_probes: dict[str, Any] | None = None,
) -> GoalEvaluation:
    """Convenience entry point — drop-in replacement for old validate_finish_claim."""
    return default_goal_evaluator.evaluate(
        contract=contract,
        verifier_status=verifier_status,
        verifier_evidence=verifier_evidence,
        after_observation=after_observation,
        device_signals=device_signals,
        finish_claim_matched=finish_claim_matched,
        reflect_named_evidence=reflect_named_evidence,
        goal_probes=goal_probes,
    )


# ----------------------------------------------------------------------
# Stage-Sealing acceptance fold (Phase C): per-criterion tri-state
# ----------------------------------------------------------------------


def _latest_entry_for(
    ledger: list[dict[str, Any]], *, contract_id: str, criterion_id: str
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("contract_id") != contract_id:
            continue
        if str(entry.get("criterion_id") or "") != criterion_id:
            continue
        latest = entry
    return latest


def _effect_event_evidence(
    ledger: list[dict[str, Any]], *, contract_id: str, criterion_id: str
) -> list[dict[str, Any]]:
    """Grounded named_evidence for one criterion from L2 effect events."""
    matched: list[dict[str, Any]] = []
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") != "effect_event":
            continue
        if entry.get("contract_id") != contract_id:
            continue
        for item in entry.get("named_evidence") or []:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("criterion") or "")
            if _normalize_criterion_name(raw_name) != _normalize_criterion_name(
                criterion_id
            ):
                continue
            screen_reference = str(item.get("screen_reference") or "").strip()
            if not screen_reference or _is_placeholder_screen_reference(
                screen_reference
            ):
                continue
            matched.append(item)
    return matched


def fold_acceptance_verdicts(
    *,
    contract: GoalContract,
    ledger: list[dict[str, Any]],
    contract_id: str,
    screen_id: str,
    observation_epoch: int,
    finish_claim_matched: list[str],
    judge_verdicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stage-Sealing per-criterion fold (Phase C).

    Replaces the all-or-nothing finish evaluation with a per-criterion
    tri-state fold. Authority order per criterion:

    1. seal (stage sealed, authoritative) → ``satisfied``
    2. L1 digest closure (mechanical raw-text record at a trusted
       observation) → ``satisfied``
    3. trusted latched evidence (ever-matched) → ``satisfied``
    4. L2 grounded effect-event named_evidence → ``satisfied``
    5. L3 judge verdict → as reported (``satisfied`` / ``unknown`` /
       ``contradicted``)
    6. otherwise → ``unknown``

    Positive counter-observations (``contradicted``) override lower tiers at
    their own tier; ``unknown``/absence never upgrades to success (P0 #13a).
    """
    from phone_agent.graph.goal_evidence import (
        criterion_satisfied_by_digest,
        ever_matched,
        remap_ledger_for_contract,
        seal_records_for_contract,
    )

    ledger = remap_ledger_for_contract(
        ledger,
        contract_id=contract_id,
        criteria={item.name: item for item in contract.success_criteria},
        task_plan=contract.task_plan,
    )
    seals = seal_records_for_contract(ledger, contract=contract, contract_id=contract_id)
    seal_by_criterion: dict[str, dict[str, Any]] = {}
    for record in seals:
        for name in record["criteria_sealed"]:
            seal_by_criterion[name] = record

    judge_map: dict[str, dict[str, Any]] = {}
    for verdict in judge_verdicts or []:
        if not isinstance(verdict, dict):
            continue
        normalized = _normalize_criterion_name(verdict.get("criterion"))
        if not normalized:
            continue
        judge_map.setdefault(normalized, verdict)

    satisfied: list[str] = []
    unknown: list[str] = []
    contradicted: list[str] = []
    programmatic_missing: list[str] = []
    per_criterion: dict[str, dict[str, Any]] = {}

    for criterion in contract.success_criteria:
        name = criterion.name
        normalized = _normalize_criterion_name(name)
        verdict: dict[str, Any] | None = None
        # 1. seal authority
        seal = seal_by_criterion.get(name)
        if seal is not None:
            per_criterion[name] = {
                "status": "satisfied",
                "reason": "sealed_by_stage",
                "seal": {
                    key: seal[key]
                    for key in (
                        "stage_id",
                        "criteria_sealed",
                        "evidence_refs",
                        "screen_id",
                        "step",
                        "sealed_at",
                        "semantic_key",
                    )
                    if key in seal
                },
            }
            satisfied.append(name)
            continue
        # 2. L1 mechanical closure (raw-text predicates only)
        closed, digest = criterion_satisfied_by_digest(
            ledger, contract_id=contract_id, criterion=criterion
        )
        if closed:
            per_criterion[name] = {
                "status": "satisfied",
                "reason": "l1_digest_closed",
                "digest_screen_id": digest.get("screen_id") if digest else None,
            }
            satisfied.append(name)
            continue
        # judge criteria (model tier) and trajectory-scoped programmatic
        # criteria are trajectory properties: a trusted matched observation
        # latches them permanently (positive contradiction later unlocks).
        is_judge = not _is_self_observable(criterion)
        trajectory_scoped = is_judge or criterion.freshness == "trajectory"
        if trajectory_scoped:
            # 3. trusted latched evidence
            latch = ever_matched(
                ledger, criterion_id=name, contract_id=contract_id
            )
            if latch.latched:
                per_criterion[name] = {
                    "status": "satisfied",
                    "reason": "evidence_latched",
                    "latched_epoch": latch.matched_epoch,
                    "latched_screen_id": latch.matched_screen_id,
                }
                satisfied.append(name)
                continue
            latest_status = str(
                (_latest_entry_for(ledger, contract_id=contract_id, criterion_id=name) or {})
                .get("status")
                or "unknown"
            )
            if latest_status == "contradicted":
                per_criterion[name] = {
                    "status": "contradicted",
                    "reason": "evidence_contradicted",
                }
                contradicted.append(name)
                continue
            # 3b. A trusted ``matched`` entry (typed provider fact at a trusted
            # observation, e.g. L1 mechanical raw-text record) settles a
            # trajectory-scoped criterion directly. The plan-side latch is
            # target-app gated, so this direct read is what restores the legacy
            # typed-fold behaviour for judge criteria whose fact was collected
            # at the acceptance observation.
            if latest_status == "matched":
                entry = _latest_entry_for(
                    ledger, contract_id=contract_id, criterion_id=name
                )
                per_criterion[name] = {
                    "status": "satisfied",
                    "reason": "evidence_matched",
                    "matched_epoch": entry.get("observation_epoch"),
                    "matched_screen_id": entry.get("screen_id"),
                }
                satisfied.append(name)
                continue
        if is_judge:
            # 4. L2 grounded effect-event named_evidence (model tier)
            l2 = _effect_event_evidence(
                ledger, contract_id=contract_id, criterion_id=name
            )
            if l2:
                per_criterion[name] = {
                    "status": "satisfied",
                    "reason": "l2_effect_event",
                    "screen_references": [
                        str(item.get("screen_reference") or "")[:128] for item in l2
                    ],
                }
                satisfied.append(name)
                continue
            # 5. L3 judge verdict
            verdict = judge_map.get(normalized)
            if verdict is not None:
                # Legacy named_evidence items carry a screen_reference; an
                # ungrounded (placeholder) reference cannot settle a criterion
                # (W1-A provenance rule preserved). New-contract verdicts
                # carry no reference and are accepted as the judge's statement.
                screen_ref = str(verdict.get("screen_reference") or "").strip()
                if screen_ref and _is_placeholder_screen_reference(screen_ref):
                    per_criterion[name] = {
                        "status": "unknown",
                        "reason": "judge_verdict_ungrounded",
                    }
                    unknown.append(name)
                    continue
                status = str(verdict.get("status") or "unknown")
                per_criterion[name] = {
                    "status": status,
                    "reason": "judge_verdict",
                    "observed_value": (
                        str(verdict.get("observed_value") or "")[:200]
                        if verdict.get("observed_value") is not None
                        else None
                    ),
                }
                if status == "satisfied":
                    satisfied.append(name)
                elif status == "contradicted":
                    contradicted.append(name)
                else:
                    unknown.append(name)
                continue
            # 6. otherwise unknown
            per_criterion[name] = {
                "status": "unknown",
                "reason": "no_evidence_no_judgement",
            }
            unknown.append(name)
            continue
        # programmatic (self-observable, current_observation) criteria keep
        # strict freshness: only the CURRENT screen's observation settles them
        # (P0 #13a fail-closed; trajectory permanence does not apply).
        entry = _latest_entry_for(
            ledger, contract_id=contract_id, criterion_id=name
        )
        if entry is None:
            per_criterion[name] = {"status": "unknown", "reason": "criterion_unobserved"}
            unknown.append(name)
            continue
        if (
            entry.get("screen_id") != screen_id
            or entry.get("observation_epoch") != observation_epoch
        ):
            per_criterion[name] = {
                "status": "unknown",
                "reason": "evidence_binding_stale",
            }
            unknown.append(name)
            continue
        status = str(entry.get("status") or "unknown")
        if status == "matched":
            per_criterion[name] = {"status": "satisfied", "reason": "evidence_matched"}
            satisfied.append(name)
        elif status == "contradicted":
            per_criterion[name] = {
                "status": "contradicted",
                "reason": "evidence_contradicted",
            }
            contradicted.append(name)
        else:
            # A verifier-resolved ``missing`` (rank mismatch, wrong object,
            # app not in foreground...) is a mechanical determination, not an
            # absence of observation. It stays fail-closed ``unknown`` in the
            # tri-state but is surfaced on the legacy missing list (P0 #13a:
            # unknown never upgrades to success).
            if status == "missing":
                programmatic_missing.append(name)
            per_criterion[name] = {
                "status": "unknown",
                "reason": str(entry.get("reason_code") or "evidence_unsettled"),
            }
            unknown.append(name)

    if contradicted:
        overall: str = "contradicted"
    elif unknown:
        overall = "unknown"
    else:
        overall = "satisfied"
    return {
        "overall": overall,
        "per_criterion": per_criterion,
        "satisfied": satisfied,
        "unknown": unknown,
        "contradicted": contradicted,
        "programmatic_missing": sorted(set(programmatic_missing)),
        "seals": seals,
    }


def evaluation_from_acceptance_fold(
    fold: dict[str, Any], *, finish_claim_matched: list[str]
) -> GoalEvaluation:
    """Map a Stage-Sealing fold onto the legacy GoalEvaluation shape.

    Legacy semantics preserved: a verifier-resolved programmatic ``missing``
    (mechanical determination of non-satisfaction) lands on the legacy missing
    list alongside genuine contradictions, so ``missing_terminal_evidence``
    keeps its historical contract (existing tests unaffected).
    """
    overall = fold["overall"]
    programmatic_missing = list(fold.get("programmatic_missing") or [])
    if overall == "satisfied":
        status: Literal["success", "failure", "unknown"] = "success"
        missing: list[str] = []
        soft = list(fold["unknown"])
    else:
        missing = sorted(set(fold["contradicted"]) | set(programmatic_missing))
        soft = list(fold["unknown"])
        status = "failure" if overall == "contradicted" else "unknown"
    return GoalEvaluation(
        status=status,
        matched=list(fold["satisfied"]),
        missing=missing,
        soft_matched=soft,
        evidence={
            "per_criterion": fold["per_criterion"],
            "fold": "stage_sealing_v1",
            "seals": fold["seals"],
            "finish_claim_matched": sorted(finish_claim_matched),
        },
    )
