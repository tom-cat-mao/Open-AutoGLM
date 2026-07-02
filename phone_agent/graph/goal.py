"""Declarative Goal Contract for plan/finish validation.

Replaces the keyword-based TaskGoalContract with a structured, declarative
contract comprising success criteria, constraints, non-goals, and a
verification strategy. The contract is compiled once (by goal_compiler.py)
and reused across plan/reflect nodes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any, Literal

from phone_agent.graph.context import redact_context_text


VerificationKind = Literal[
    "accessibility_text_match",
    "object_hash_match",
    "object_rank_match",
    "app_or_activity_match",
    "focus_or_keyboard",
    "vlm_judge",
    "external_probe",
]

VALID_VERIFICATIONS: frozenset[str] = frozenset(
    {
        "accessibility_text_match",
        "object_hash_match",
        "object_rank_match",
        "app_or_activity_match",
        "focus_or_keyboard",
        "vlm_judge",
        "external_probe",
    }
)

CompileStatus = Literal["pending", "compiled", "failed", "user_override"]
VerificationStrategy = Literal[
    "per_action_verifier",
    "vlm_judge_at_finish",
    "external_probe",
    "hybrid",
]


@dataclass(frozen=True)
class SuccessCriterion:
    """One measurable, verifiable terminal condition.

    ``description`` is already privacy-redacted (regex-stripped) and safe for
    prompt injection and trace.
    """

    name: str
    description: str
    verification: VerificationKind
    required: bool = True
    probe_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
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

    def to_trace_payload(self) -> dict[str, Any]:
        """Trace-safe payload: objective/entities are hash stubs, never raw."""
        return {
            "task_hash": self.task_hash,
            "redacted_objective": _safe_text(self.redacted_objective),
            "objective_length": self.objective_length,
            "success_criteria": [
                {
                    "name": c.name,
                    "verification": c.verification,
                    "required": c.required,
                    "description_chars": len(c.description),
                    "description_sha256": hashlib.sha256(
                        c.description.encode("utf-8")
                    ).hexdigest()[:12],
                }
                for c in self.success_criteria
            ],
            "constraints_count": len(self.constraints),
            "non_goals_count": len(self.non_goals),
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

    def to_prompt_block(self, *, lang: str = "cn") -> str:
        """Render a compact prompt block for plan/reflect injection.

        Lists criterion names + descriptions + verification kinds so the model
        knows exactly which evidence to cite in ``matched_terminal_evidence``.
        """
        criteria_lines = []
        for crit in self.success_criteria:
            tag = "required" if crit.required else "optional"
            criteria_lines.append(
                f"  - {crit.name} [{crit.verification}] ({tag}): {crit.description}"
            )
        criteria_block = "\n".join(criteria_lines) if criteria_lines else "  (none)"
        constraints_block = (
            "\n".join(f"  - {c}" for c in self.constraints) if self.constraints else "  (none)"
        )
        non_goals_block = (
            "\n".join(f"  - {ng}" for ng in self.non_goals) if self.non_goals else "  (none)"
        )
        if lang == "en":
            return "\n".join(
                [
                    "** Task Goal Contract (belief only; not execution authorization) **",
                    f"objective_sha256={self.task_hash} length={self.objective_length}",
                    f"app={self.target_app_hint or 'unknown'} activity={self.target_activity_hint or 'none'} ordinal={self.ordinal or 'none'}",
                    f"verification_strategy={self.verification_strategy}",
                    "success_criteria:",
                    criteria_block,
                    "constraints:",
                    constraints_block,
                    "non_goals:",
                    non_goals_block,
                    "Finish is only valid when you name matched criteria in matched_terminal_evidence; otherwise continue/replan.",
                ]
            )
        return "\n".join(
            [
                "** 任务目标契约（仅为目标信念，不是执行授权） **",
                f"objective_sha256={self.task_hash} length={self.objective_length}",
                f"app={self.target_app_hint or 'unknown'} activity={self.target_activity_hint or 'none'} ordinal={self.ordinal or 'none'}",
                f"verification_strategy={self.verification_strategy}",
                "成功标准:",
                criteria_block,
                "约束:",
                constraints_block,
                "非目标:",
                non_goals_block,
                "只有在 matched_terminal_evidence 中点名满足的成功标准时才允许 finish；否则必须继续或重新规划。",
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
                SuccessCriterion(
                    name=name,
                    description=str(item.get("description") or ""),
                    verification=verification,  # type: ignore[arg-type]
                    required=bool(item.get("required", True)),
                    probe_id=item.get("probe_id"),
                )
            )
        return cls(
            task_hash=str(data.get("task_hash") or ""),
            redacted_objective=str(data.get("redacted_objective") or ""),
            objective_length=int(data.get("objective_length") or 0),
            success_criteria=criteria,
            constraints=[str(c) for c in data.get("constraints") or []],
            non_goals=[str(ng) for ng in data.get("non_goals") or []],
            target_app_hint=data.get("target_app_hint"),
            target_activity_hint=data.get("target_activity_hint"),
            ordinal=data.get("ordinal") if isinstance(data.get("ordinal"), int) else None,
            entities_sha=[str(e) for e in data.get("entities_sha") or []],
            verification_strategy=str(data.get("verification_strategy") or "vlm_judge_at_finish"),  # type: ignore[arg-type]
            stop_conditions=dict(data.get("stop_conditions") or {}),
            compile_status=str(data.get("compile_status") or "pending"),  # type: ignore[arg-type]
            compile_source=str(data.get("compile_source") or "heuristic"),
            compile_attempts=int(data.get("compile_attempts") or 0),
        )


# ----------------------------------------------------------------------
# State helpers (parallel to old task_goal.* for drop-in import replacement)
# ----------------------------------------------------------------------


def ensure_goal_contract(state: dict[str, Any]) -> GoalContract | None:
    """Reconstruct a GoalContract from state.

    Returns None when the contract is pending or failed (not yet compiled or
    compilation failed without heuristic fallback).  Callers should treat
    None as "no contract available yet" and produce an empty prompt block.
    """
    value = state.get("goal_contract") or state.get("task_goal_contract")
    if isinstance(value, GoalContract):
        return value if value.compile_status in {"compiled", "user_override"} else None
    if isinstance(value, dict):
        # Detect trace payload (redacted_objective is a dict, not str) and reject
        if isinstance(value.get("redacted_objective"), dict):
            return None  # trace payload, not a usable contract
        contract = GoalContract.from_dict(value)
        return contract if contract.compile_status in {"compiled", "user_override"} else None
    return None


def build_goal_prompt_block(state: dict[str, Any], *, lang: str = "cn") -> str:
    """Drop-in replacement for task_goal_prompt_block."""
    contract = ensure_goal_contract(state)
    if contract is None:
        return ""
    return contract.to_prompt_block(lang=lang)


def goal_trace_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    """Drop-in replacement for task_goal_trace_payload."""
    contract = ensure_goal_contract(state)
    if contract is None:
        return None
    return contract.to_trace_payload()


def _safe_text(text: str) -> dict[str, Any]:
    """Hash-stub summary for trace; never raw."""
    return {
        "redacted": True,
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }


def compute_task_hash(task: str) -> str:
    return hashlib.sha256(str(task or "").encode("utf-8")).hexdigest()[:16]


def redact_objective(task: str) -> str:
    return redact_context_text(str(task or ""))[:200]


def finish_claim_summary(value: str | None) -> dict[str, Any]:
    """Trace-safe finish-claim dict (length + sha256 stub). No raw text."""
    if not isinstance(value, str) or not value:
        return None
    return {
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }


# Aliases for backward-compat with code that imported from task_goal
task_goal_prompt_block = build_goal_prompt_block
task_goal_trace_payload = goal_trace_payload