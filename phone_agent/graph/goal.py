"""Declarative Goal Contract for plan/finish validation.

Replaces the keyword-based TaskGoalContract with a structured, declarative
contract comprising success criteria, constraints, non-goals, and a
verification strategy. The contract is compiled once (by goal_compiler.py)
and reused across plan/reflect nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, get_args

from phone_agent.graph.context import redact_context_text
from phone_agent.graph.goal_binding import compute_task_binding
from phone_agent.graph.predicates import (
    CORE_PREDICATE_CATALOG,
    PredicateSpec,
)

VerificationKind = Literal[
    "accessibility_text_match",
    "object_hash_match",
    "object_rank_match",
    "app_or_activity_match",
    "focus_or_keyboard",
    "toggle_state_match",
    "vlm_judge",
    "external_probe",
]

# Derived from the Literal so the runtime allowlist and the type can never
# list different verification kinds.
VALID_VERIFICATIONS: frozenset[str] = frozenset(get_args(VerificationKind))

CompileStatus = Literal["pending", "compiled", "failed", "user_override"]
VerificationStrategy = Literal[
    "per_action_verifier",
    "vlm_judge_at_finish",
    "external_probe",
    "hybrid",
]


@dataclass(frozen=True)
class CriterionSpec:
    """One measurable, verifiable terminal condition.

    ``description`` is already privacy-redacted (regex-stripped) and safe for
    prompt injection and trace.
    """

    name: str
    description: str
    verification: VerificationKind
    required: bool = True
    probe_id: str | None = None
    predicate: PredicateSpec | None = None
    scope: Literal["terminal", "trajectory"] = "terminal"
    allowed_sources: tuple[str, ...] = ()
    freshness: Literal["current_observation", "trajectory"] = "current_observation"
    ambiguity_policy: Literal["unknown", "contradicted"] = "unknown"
    recovery_policy: str | None = None
    dependencies: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = {
            "name": self.name,
            "description": self.description,
            "verification": self.verification,
            "required": self.required,
            "probe_id": self.probe_id,
            "scope": self.scope,
            "allowed_sources": list(self.allowed_sources),
            "freshness": self.freshness,
            "ambiguity_policy": self.ambiguity_policy,
            "recovery_policy": self.recovery_policy,
            "dependencies": list(self.dependencies),
            "contradictions": list(self.contradictions),
        }
        if self.predicate is not None:
            definition = CORE_PREDICATE_CATALOG.get(self.predicate.predicate_id)
            value["predicate"] = {
                "predicate_id": self.predicate.predicate_id,
                "matcher_id": self.predicate.matcher_id,
                "privacy_class": self.predicate.privacy_class,
                "expected_value": (
                    self.predicate.expected_value
                    if definition.projection.state == "full"
                    else None
                ),
                "expected_value_projection": definition.projection.state,
            }
        return value


# Compatibility name retained while callers migrate to CriterionSpec.
SuccessCriterion = CriterionSpec


@dataclass(frozen=True)
class GoalContract:
    """Durable, trace-safe task goal contract.

    Independent from message history compaction — survives context window
    trimming.  ``redacted_objective`` / ``entities_sha`` are privacy-stripped;
    raw task text never lives in this object.
    """

    task_hash: str
    redacted_objective: str
    objective_length: int
    success_criteria: list[CriterionSpec] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    target_app_hint: str | None = None
    target_activity_hint: str | None = None
    ordinal: int | None = None
    entities_sha: list[str] = field(default_factory=list)
    verification_strategy: VerificationStrategy = "vlm_judge_at_finish"
    stop_conditions: dict[str, Any] = field(default_factory=dict)
    compile_status: CompileStatus = "pending"
    compile_source: str = "heuristic"
    compile_attempts: int = 0

    def __post_init__(self) -> None:
        """Normalize all prompt/state text through the privacy redactor."""

        object.__setattr__(
            self, "redacted_objective", redact_context_text(self.redacted_objective)
        )
        object.__setattr__(
            self,
            "success_criteria",
            [
                replace(item, description=redact_context_text(item.description)[:300])
                for item in self.success_criteria
            ],
        )
        object.__setattr__(
            self,
            "constraints",
            [redact_context_text(str(item))[:300] for item in self.constraints[:8]],
        )
        object.__setattr__(
            self,
            "non_goals",
            [redact_context_text(str(item))[:300] for item in self.non_goals[:8]],
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_hash": self.task_hash,
            "redacted_objective": self.redacted_objective,
            "objective_length": self.objective_length,
            "success_criteria": [c.to_dict() for c in self.success_criteria],
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "target_app_hint": self.target_app_hint,
            "target_activity_hint": self.target_activity_hint,
            "ordinal": self.ordinal,
            "entities_sha": list(self.entities_sha),
            "verification_strategy": self.verification_strategy,
            "stop_conditions": dict(self.stop_conditions),
            "compile_status": self.compile_status,
            "compile_source": self.compile_source,
            "compile_attempts": self.compile_attempts,
        }

    def to_state_payload(self, *, runtime_reference: str | None) -> dict[str, Any]:
        """Return metadata and public predicate values safe for AgentState."""

        criteria: list[dict[str, Any]] = []
        for item in self.success_criteria:
            value = item.to_dict()
            value["description"] = ""
            criteria.append(value)
        return {
            "schema": "goal_contract_state_metadata_v1",
            "runtime_reference": runtime_reference,
            "objective_length": self.objective_length,
            "success_criteria": criteria,
            "constraints_count": len(self.constraints),
            "non_goals_count": len(self.non_goals),
            "target_app_hint": self.target_app_hint,
            "target_activity_hint": self.target_activity_hint,
            "ordinal": self.ordinal,
            "verification_strategy": self.verification_strategy,
            "stop_conditions": dict(self.stop_conditions),
            "compile_status": self.compile_status,
            "compile_source": self.compile_source,
            "compile_attempts": self.compile_attempts,
        }

    def to_trace_payload(self) -> dict[str, Any]:
        """Return semantic-free Goal metadata for tracing."""
        return {
            "schema": "goal_contract_trace_metadata_v1",
            "objective_length": self.objective_length,
            "success_criteria": [
                {
                    "name": c.name,
                    "verification": c.verification,
                    "required": c.required,
                    "description_chars": len(c.description),
                    "predicate_id": (
                        c.predicate.predicate_id if c.predicate is not None else None
                    ),
                    "matcher_id": (
                        c.predicate.matcher_id if c.predicate is not None else None
                    ),
                }
                for c in self.success_criteria
            ],
            "constraints_count": len(self.constraints),
            "non_goals_count": len(self.non_goals),
            "target_app_hint": self.target_app_hint,
            "target_activity_hint": self.target_activity_hint,
            "ordinal": self.ordinal,
            "verification_strategy": self.verification_strategy,
            "stop_conditions": dict(self.stop_conditions),
            "compile_status": self.compile_status,
            "compile_source": self.compile_source,
            "compile_attempts": self.compile_attempts,
        }

    def to_prompt_block(self, *, lang: str = "cn") -> str:
        """Render a compact prompt block for plan/reflect injection.

        Every criterion is listed, because all of them describe the goal and
        are useful for planning. Each is tagged with who settles it: criteria
        the system verifies from device/registry truth are marked ``auto`` so
        the model neither has to cite them nor guess an internal value, and
        only ``judge`` criteria need to appear in ``matched_terminal_evidence``.
        """
        from phone_agent.graph.goal_evaluator import _is_self_observable

        criteria_lines = []
        judge_names: list[str] = []
        for crit in self.success_criteria:
            tag = "required" if crit.required else "optional"
            if _is_self_observable(crit):
                arbiter = "auto"
            else:
                arbiter = "judge"
                judge_names.append(crit.name)
            criteria_lines.append(
                f"  - {crit.name} [{crit.verification}] ({tag}, {arbiter}):"
                f" {crit.description}"
            )
        criteria_block = "\n".join(criteria_lines) if criteria_lines else "  (none)"
        judge_block = ", ".join(judge_names) if judge_names else "none"
        constraints_block = (
            "\n".join(f"  - {c}" for c in self.constraints)
            if self.constraints
            else "  (none)"
        )
        non_goals_block = (
            "\n".join(f"  - {ng}" for ng in self.non_goals)
            if self.non_goals
            else "  (none)"
        )
        if lang == "en":
            return "\n".join(
                [
                    "** Task Goal Contract (belief only; not execution authorization) **",
                    f"objective_length={self.objective_length}",
                    f"app={self.target_app_hint or 'unknown'} activity={self.target_activity_hint or 'none'} ordinal={self.ordinal or 'none'}",
                    f"verification_strategy={self.verification_strategy}",
                    "success_criteria:",
                    criteria_block,
                    "constraints:",
                    constraints_block,
                    "non_goals:",
                    non_goals_block,
                    "[auto] criteria are verified from device state — do not cite them.",
                    (
                        "Finish is only valid when you name the [judge] criteria you can "
                        f"see satisfied in matched_terminal_evidence ({judge_block}); "
                        "otherwise continue/replan."
                    ),
                ]
            )
        return "\n".join(
            [
                "** 任务目标契约（仅为目标信念，不是执行授权） **",
                f"objective_length={self.objective_length}",
                f"app={self.target_app_hint or 'unknown'} activity={self.target_activity_hint or 'none'} ordinal={self.ordinal or 'none'}",
                f"verification_strategy={self.verification_strategy}",
                "成功标准:",
                criteria_block,
                "约束:",
                constraints_block,
                "非目标:",
                non_goals_block,
                "[auto] 标准由系统读取设备状态自行核验，不需要你点名或回报。",
                (
                    "只有在 matched_terminal_evidence 中点名你确实看到已满足的 [judge] "
                    f"标准时才允许 finish（{judge_block}）；否则必须继续或重新规划。"
                ),
            ]
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalContract":
        """Reconstruct from a state-stored dict."""
        criteria = []
        for item in data.get("success_criteria") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            verification = str(item.get("verification") or "vlm_judge")
            if verification not in VALID_VERIFICATIONS:
                verification = "vlm_judge"
            criteria.append(
                CriterionSpec(
                    name=name,
                    description=str(item.get("description") or ""),
                    verification=verification,  # type: ignore[arg-type]
                    required=bool(item.get("required", True)),
                    probe_id=item.get("probe_id"),
                    predicate=_predicate_from_dict(item.get("predicate")),
                    scope=(
                        item.get("scope")
                        if item.get("scope") in {"terminal", "trajectory"}
                        else "terminal"
                    ),
                    allowed_sources=tuple(
                        str(value) for value in item.get("allowed_sources") or []
                    ),
                    freshness=(
                        item.get("freshness")
                        if item.get("freshness")
                        in {"current_observation", "trajectory"}
                        else "current_observation"
                    ),
                    ambiguity_policy=(
                        item.get("ambiguity_policy")
                        if item.get("ambiguity_policy") in {"unknown", "contradicted"}
                        else "unknown"
                    ),
                    recovery_policy=item.get("recovery_policy"),
                    dependencies=tuple(
                        str(value) for value in item.get("dependencies") or []
                    ),
                    contradictions=tuple(
                        str(value) for value in item.get("contradictions") or []
                    ),
                )
            )
        return cls(
            task_hash=str(data.get("task_hash") or data.get("runtime_reference") or ""),
            redacted_objective=str(data.get("redacted_objective") or ""),
            objective_length=int(data.get("objective_length") or 0),
            success_criteria=criteria,
            constraints=[str(c) for c in data.get("constraints") or []],
            non_goals=[str(ng) for ng in data.get("non_goals") or []],
            target_app_hint=data.get("target_app_hint"),
            target_activity_hint=data.get("target_activity_hint"),
            ordinal=(
                data.get("ordinal") if isinstance(data.get("ordinal"), int) else None
            ),
            entities_sha=[str(e) for e in data.get("entities_sha") or []],
            verification_strategy=str(
                data.get("verification_strategy") or "vlm_judge_at_finish"
            ),  # type: ignore[arg-type]
            stop_conditions=dict(data.get("stop_conditions") or {}),
            compile_status=str(data.get("compile_status") or "pending"),  # type: ignore[arg-type]
            compile_source=str(data.get("compile_source") or "heuristic"),
            compile_attempts=int(data.get("compile_attempts") or 0),
        )


# ----------------------------------------------------------------------
# State helpers (parallel to old task_goal.* for drop-in import replacement)
# ----------------------------------------------------------------------


def ensure_goal_contract(
    state: dict[str, Any], config: dict[str, Any] | None = None
) -> GoalContract | None:
    """Reconstruct a GoalContract from state.

    Returns None when the contract is pending or failed (not yet compiled or
    compilation failed without heuristic fallback).  Callers should treat
    None as "no contract available yet" and produce an empty prompt block.
    """
    value = state.get("goal_contract") or state.get("task_goal_contract")
    if isinstance(value, dict):
        if value.get("schema") == "goal_contract_trace_metadata_v1":
            return None
        if value.get("schema") == "goal_contract_state_metadata_v1":
            reference = value.get("runtime_reference")
            configurable = (config or {}).get("configurable", {})
            runtime_context = configurable.get("runtime_goal_context")
            if not isinstance(reference, str) or runtime_context is None:
                return None
            try:
                return runtime_context.resolve(
                    reference_id=reference, task=str(state.get("task") or "")
                ).contract
            except (AttributeError, ValueError):
                return None
        reference = value.get("runtime_reference")
        configurable = (config or {}).get("configurable", {})
        runtime_context = configurable.get("runtime_goal_context")
        if isinstance(reference, str) and runtime_context is not None:
            try:
                return runtime_context.resolve(
                    reference_id=reference, task=str(state.get("task") or "")
                ).contract
            except (AttributeError, ValueError):
                return None
    if isinstance(value, GoalContract):
        return value if value.compile_status in {"compiled", "user_override"} else None
    configurable = (config or {}).get("configurable", {})
    if isinstance(value, dict) and configurable.get(
        "allow_legacy_goal_state_for_tests", False
    ):
        # Detect trace payload (redacted_objective is a dict, not str) and reject
        if isinstance(value.get("redacted_objective"), dict):
            return None  # trace payload, not a usable contract
        contract = GoalContract.from_dict(value)
        return (
            contract
            if contract.compile_status in {"compiled", "user_override"}
            else None
        )
    return None


def build_goal_prompt_block(
    state: dict[str, Any], *, lang: str = "cn", config: dict[str, Any] | None = None
) -> str:
    """Drop-in replacement for task_goal_prompt_block."""
    contract = ensure_goal_contract(state, config)
    if contract is None:
        return ""
    return contract.to_prompt_block(lang=lang)


def goal_trace_payload(
    state: dict[str, Any], config: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Drop-in replacement for task_goal_trace_payload."""
    contract = ensure_goal_contract(state, config)
    if contract is None:
        return None
    return contract.to_trace_payload()


def goal_runtime_reference(state: dict[str, Any]) -> str:
    """Return an opaque runtime binding or an explicit unbound marker."""

    value = state.get("goal_contract") or {}
    if isinstance(value, dict) and isinstance(value.get("runtime_reference"), str):
        return value["runtime_reference"]
    return "unbound-runtime-contract"


def compute_task_hash(task: str) -> str:
    """Compatibility alias for the shared internal task binding."""

    return compute_task_binding(task)


def redact_objective(task: str) -> str:
    return redact_context_text(str(task or ""))[:200]


def finish_claim_summary(value: str | None) -> dict[str, Any]:
    """Return non-identifying finish-claim metadata."""
    if not isinstance(value, str) or not value:
        return None
    return {
        "length": len(value),
        "present": True,
    }


# Aliases for backward-compat with code that imported from task_goal
task_goal_prompt_block = build_goal_prompt_block
task_goal_trace_payload = goal_trace_payload


def _predicate_from_dict(value: Any) -> PredicateSpec | None:
    if not isinstance(value, dict):
        return None
    predicate_id = value.get("predicate_id")
    if not isinstance(predicate_id, str):
        return None
    # Runtime-only private expected values are intentionally not rehydrated
    # from AgentState. They require a trusted runtime value source.
    expected = value.get("expected_value")
    if expected is None:
        return None
    try:
        return CORE_PREDICATE_CATALOG.create_spec(predicate_id, expected)
    except ValueError:
        return None
