"""Declarative Goal Contract for plan/finish validation.

Replaces the keyword-based TaskGoalContract with a structured, declarative
contract comprising success criteria, constraints, non-goals, and a
verification strategy. The contract is compiled once (by goal_compiler.py)
and reused across plan/reflect nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
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
LEGACY_SHA256_STUB_PATTERN = re.compile(r"sha256:([0-9a-fA-F]{8,64})")

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
class TaskStage:
    """One page-level execution stage of a task plan (W2).

    Belief only — never a gate. ``objective``/``fallback`` are free-text and
    are regex-redacted by ``GoalContract.__post_init__`` before they can reach
    any prompt/state/trace payload. ``done_criteria`` reference names of the
    contract's own ``success_criteria``; unknown names are rejected at compile
    time (see ``task_plan_validation_errors``) and dropped by ``from_dict``.
    """

    stage_id: str
    objective: str
    done_criteria: tuple[str, ...]
    fallback: str
    index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "objective": self.objective,
            "done_criteria": list(self.done_criteria),
            "fallback": self.fallback,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskStage":
        return cls(
            stage_id=str(data.get("stage_id") or ""),
            objective=str(data.get("objective") or ""),
            done_criteria=tuple(
                str(value) for value in (data.get("done_criteria") or [])
            ),
            fallback=str(data.get("fallback") or ""),
            index=int(data.get("index") or 0),
        )


# Verification kinds whose criteria are trivially satisfied once the target app
# is in the foreground — they may not be the ONLY done-criterion of a stage
# (W2 T2: prevents phantom stage advancement). ``app.foreground_identity`` is
# the canonical always-true auto standard.
STAGE_TRIVIAL_VERIFICATION_KINDS = frozenset({"app_or_activity_match"})


def _criterion_is_stage_substantive(criterion: CriterionSpec | None) -> bool:
    """Whether a done criterion can actually gate stage advancement.

    A criterion is ``substantive`` unless it is an app-foreground check (the
    canonical always-true auto standard): ``app_or_activity_match`` verification
    or a predicate whose id starts with ``app.foreground``. A stage whose done
    criteria are ALL substantive-less is rejected at compile time so a plan
    cannot pretend to progress while only the target app is visible.
    """

    if criterion is None:
        return False
    if criterion.verification == "vlm_judge":
        return True
    if criterion.verification in STAGE_TRIVIAL_VERIFICATION_KINDS:
        return False
    predicate = criterion.predicate
    if predicate is not None and predicate.predicate_id.startswith("app.foreground"):
        return False
    return True


def task_plan_validation_errors(
    task_plan: tuple[TaskStage, ...] | None,
    *,
    criterion_names: list[str],
    criteria: dict[str, CriterionSpec] | None = None,
) -> list[str]:
    """Return stable validation errors for a task plan, or [] when valid.

    Rules (W2 T1/T2): stage ids and indices are unique; every done criterion
    name must exist in the contract's success_criteria; every stage must carry
    at least one substantive (non always-true-auto) done criterion. ``None``
    plan is always valid (no plan is a legal degraded state).
    """

    if not task_plan:
        return []
    names = set(criterion_names)
    criteria_map = criteria or {}
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_indices: set[int] = set()
    for stage in task_plan:
        if not isinstance(stage, TaskStage):
            errors.append("stage_not_typed")
            continue
        if not stage.stage_id:
            errors.append(f"stage_{stage.index}:empty_stage_id")
        elif stage.stage_id in seen_ids:
            errors.append(f"stage_{stage.index}:duplicate_stage_id:{stage.stage_id}")
        else:
            seen_ids.add(stage.stage_id)
        if stage.index in seen_indices:
            errors.append(f"stage_{stage.index}:duplicate_index")
        else:
            seen_indices.add(stage.index)
        if not stage.done_criteria:
            errors.append(f"stage_{stage.index}:empty_done_criteria")
            continue
        unknown = [name for name in stage.done_criteria if name not in names]
        if unknown:
            errors.append(
                f"stage_{stage.index}:unknown_done_criteria:{','.join(sorted(unknown))}"
            )
        substantive = any(
            _criterion_is_stage_substantive(criteria_map.get(name))
            for name in stage.done_criteria
        )
        if not substantive:
            errors.append(f"stage_{stage.index}:trivial_only_done_criteria")
    return errors


def validate_task_plan(
    task_plan: tuple[TaskStage, ...] | None,
    *,
    criterion_names: list[str],
    criteria: dict[str, CriterionSpec] | None = None,
) -> bool:
    """Boolean form of :func:`task_plan_validation_errors` (compile gate)."""

    return not task_plan_validation_errors(
        task_plan, criterion_names=criterion_names, criteria=criteria
    )


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
    task_plan: tuple[TaskStage, ...] | None = None

    def __post_init__(self) -> None:
        """Normalize all prompt/state text through the privacy redactor."""

        object.__setattr__(
            self, "redacted_objective", redact_context_text(self.redacted_objective)
        )
        if self.task_plan is not None:
            object.__setattr__(
                self,
                "task_plan",
                tuple(
                    replace(
                        stage,
                        objective=redact_context_text(stage.objective)[:200],
                        fallback=redact_context_text(stage.fallback)[:200],
                    )
                    for stage in self.task_plan
                ),
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
            "task_plan": (
                [stage.to_dict() for stage in self.task_plan]
                if self.task_plan is not None
                else None
            ),
        }

    def _task_plan_metadata(self) -> list[dict[str, Any]] | None:
        """Privacy-safe task_plan projection shared by state and trace payloads.

        Only denatured metadata leaves the runtime reference: the objective is
        regex-redacted (done in ``__post_init__``), done_criteria are criterion
        names only, and the fallback sentence is not carried. The full plan
        (including fallback) lives exclusively in the runtime Goal reference.
        """

        if self.task_plan is None:
            return None
        return [
            {
                "stage_id": stage.stage_id,
                "objective": stage.objective,
                "done_criteria": list(stage.done_criteria),
                "index": stage.index,
            }
            for stage in self.task_plan
        ]

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
            "task_plan": self._task_plan_metadata(),
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
            "task_plan": self._task_plan_metadata(),
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
        task_plan_block = _render_task_plan_block(self.task_plan, lang=lang)
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
                    task_plan_block,
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
                task_plan_block,
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
            task_plan=_task_plan_from_dict(
                data.get("task_plan"),
                criterion_names=[item.name for item in criteria],
            ),
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


def _task_plan_from_dict(
    value: Any, *, criterion_names: list[str]
) -> tuple[TaskStage, ...] | None:
    """Reconstruct a task plan from a stored dict, dropping invalid stages.

    ``from_dict`` is lossy-tolerant (resume/legacy path): stages referencing
    unknown criterion names are dropped rather than raising, so a stale plan
    degrades to fewer stages / None instead of failing the run. Compile-time
    strictness lives in the compilers (``task_plan_validation_errors``).
    """

    if value is None:
        return None
    if not isinstance(value, list) or not value:
        return None
    names = set(criterion_names)
    stages: list[TaskStage] = []
    seen_indices: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        stage = TaskStage.from_dict(item)
        if not stage.stage_id or stage.index in seen_indices:
            continue
        if not stage.done_criteria or any(
            name not in names for name in stage.done_criteria
        ):
            continue
        seen_indices.add(stage.index)
        stages.append(stage)
    return tuple(stages) if stages else None


def _render_task_plan_block(
    task_plan: tuple[TaskStage, ...] | None, *, lang: str
) -> str:
    """Render the full task plan for the static goal-contract prompt block.

    Static, task-scoped and cacheable: it carries the whole reference path
    (stage ordinal + page-level objective + done-signal names + fallback) with
    the explicit belief-not-authority annotation. The current-stage focus is
    deliberately NEVER rendered here — that is the dynamic context block's job
    (W2 T5: dynamic stage info never enters the static block).
    """

    if not task_plan:
        return ""
    total = len(task_plan)
    if lang == "en":
        lines = ["task_plan (reference path only; the screenshot prevails):"]
        for stage in task_plan:
            signals = ", ".join(stage.done_criteria) or "none"
            line = (
                f"  stage {stage.index + 1}/{total}: {stage.objective} "
                f"-> done when: {signals}"
            )
            if stage.fallback:
                line += f" | if stuck: {stage.fallback}"
            lines.append(line)
        return "\n".join(lines)
    lines = ["任务阶段规划（参考路径，以截图为准）："]
    for stage in task_plan:
        signals = ", ".join(stage.done_criteria) or "无"
        line = (
            f"  阶段 {stage.index + 1}/{total}：{stage.objective} "
            f"-> 完成信号：{signals}"
        )
        if stage.fallback:
            line += f" | 卡住时：{stage.fallback}"
        lines.append(line)
    return "\n".join(lines)


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
