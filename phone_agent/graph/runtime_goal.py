"""Per-run, nonserializable storage for complete Goal contracts."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from phone_agent.graph.goal import GoalContract
from phone_agent.graph.goal_binding import compute_task_binding
from phone_agent.graph.goal_requirements import TaskRequirementSet


@dataclass(frozen=True)
class RuntimeGoalBinding:
    """One task-bound runtime Goal registration."""

    reference_id: str
    task_binding: str
    contract: GoalContract
    requirements: TaskRequirementSet


class RuntimeGoalContext:
    """Explicit per-invocation dependency for values forbidden from AgentState."""

    def __init__(self) -> None:
        self._binding: RuntimeGoalBinding | None = None

    def register(
        self,
        *,
        task: str,
        contract: GoalContract,
        requirements: TaskRequirementSet,
    ) -> str:
        """Bind a complete contract and requirement set to the current task."""

        task_binding = compute_task_binding(task)
        if contract.task_hash != task_binding or requirements.task_hash != task_binding:
            raise ValueError("runtime goal task binding mismatch")
        reference_id = f"goal-{uuid.uuid4().hex}"
        self._binding = RuntimeGoalBinding(
            reference_id=reference_id,
            task_binding=task_binding,
            contract=contract,
            requirements=requirements,
        )
        return reference_id

    def resolve(self, *, reference_id: str, task: str) -> RuntimeGoalBinding:
        """Resolve only the binding registered for this run and exact task meaning."""

        binding = self._binding
        if (
            binding is None
            or binding.reference_id != reference_id
            or binding.task_binding != compute_task_binding(task)
        ):
            raise ValueError("runtime goal binding unavailable")
        return binding

    def __getstate__(self) -> dict:
        raise TypeError("RuntimeGoalContext is per-run and not serializable")
