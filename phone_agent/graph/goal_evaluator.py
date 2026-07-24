"""GoalEvaluator: validates finish claims against GoalContract criteria.

Replaces the keyword-based ``validate_finish_claim``.  For each
``SuccessCriterion`` in the contract, checks the criterion using the
appropriate signal source:

* ``accessibility_text_match`` → sha256 stub matching against after-observation text
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
from typing import Any, Literal, Protocol

from phone_agent.graph.goal import GoalContract, SuccessCriterion
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
    """Pure typed-criterion fold over a bounded, already-matched evidence ledger."""

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
        claim_ids = set(finish_claim_matched)
        criterion_ids = {criterion.name for criterion in contract.success_criteria}
        unknown_claim_ids = sorted(claim_ids - criterion_ids)
        latest: dict[str, dict[str, Any]] = {}
        for entry in evidence_ledger:
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
        named_evidence_map = {
            str(item.get("criterion", "")): item
            for item in (reflect_named_evidence or [])
            if isinstance(item, dict)
        }
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

        return GoalEvaluation(
            status=status,
            matched=matched,
            missing=final_missing,
            soft_matched=soft_matched,
            evidence={
                "per_criterion": per_criterion,
                "goal_type": "declarative_contract",
                "ordinal": contract.ordinal,
                "verification_strategy": contract.verification_strategy,
                "finish_claim_matched": sorted(finish_matched_set),
            },
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
        if crit.predicate is not None:
            return self._check_typed_predicate(
                crit,
                contract=contract,
                finish_matched_set=finish_matched_set,
                named_evidence_map=named_evidence_map,
                vlm_not_run=vlm_not_run,
            )
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
        if crit.verification == "external_probe":
            return self._check_external_probe(crit, goal_probes)
        if crit.verification == "vlm_judge":
            return self._check_vlm_judge(
                crit, finish_matched_set, named_evidence_map, vlm_not_run
            )
        return {"status": "missing", "reason": "unknown_verification"}

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
        evidence = named_evidence_map.get(crit.name)
        if not isinstance(evidence, dict):
            return {"status": "missing", "reason": "typed_fact_missing"}
        screen_reference = str(evidence.get("screen_reference") or "").strip()
        if not screen_reference:
            return {"status": "missing", "reason": "no_screen_reference"}
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

        # Extract sha256 stubs from description (format: "sha256:xxxxxxxxxxxx")
        digest_list = self._extract_sha256_stubs(crit.description)
        if not digest_list:
            # No stubs — fall back to vlm_judge-style (can't programmatically verify)
            return {"status": "missing", "reason": "no_sha256_stubs_in_description"}
        # _match_expected_text expects items prefixed with "sha256:"
        stubs = [f"sha256:{d}" for d in digest_list]
        text_blob = _observation_text(after_observation).lower()
        matched, missing = _match_expected_text(stubs, text_blob)
        if missing:
            return {"status": "missing", "reason": "text_not_found", "missing": missing}
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
        return {"status": "missing", "reason": "no_object_hash_signal"}

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
        # Soft fallback: when the verifier could not positively identify the
        # selected object's content hash (selected_object_match is None because
        # expected_outcome was not pre-declared), but the after-observation shows
        # a detail/player surface (selected_object_detail_signal=True) and no
        # negative signal (wrong_detail_opened / same_surface_still_visible),
        # accept the ordinal match as `unknown` so a finish claim can proceed
        # without forcing the model to compute content hashes (which it cannot).
        # Programmatic contradiction signals above still override this.
        if (
            expected_rank is None
            and signals.get("selected_object_detail_signal")
            and not signals.get("wrong_detail_opened")
            and not signals.get("same_surface_still_visible")
        ):
            return {
                "status": "matched",
                "reason": f"rank_{contract.ordinal}_detail_only_soft_match",
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
        evidence = named_evidence_map.get(crit.name)
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
        import re

        return re.findall(r"sha256:([0-9a-fA-F]{6,16})", text)

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
