"""ThinPhoneAgent: v2 thin-loop assembly and run driver.

Per refactor-thin-loop-v2 §10. The agent wires a LangChain ``create_agent``
graph with the v2 middleware stack (safety HITL + image pruning + JSONL trace +
model-call limit) and drives a synchronous ``run`` loop that surfaces HITL
interrupts to a ``hitl_handler``.

Cross-module dependencies from the concurrent W-core / W-tools worktrees
(``phone_agent.v2.model``, ``phone_agent.v2.session``, ``phone_agent.v2.prompts``,
``phone_agent.v2.tools``) are imported lazily inside methods so this module can
be imported (and its pure logic unit-tested) before those files land.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage


@dataclass
class RunResult:
    """Outcome of a single :meth:`ThinPhoneAgent.run` invocation (§10)."""

    success: bool
    reason: str
    steps: int
    trace_path: str | None = None


def _first_observation_content(observation: Any, task: str) -> list[dict[str, Any]]:
    """Build the initial user message content: task text + screenshot image."""
    content: list[dict[str, Any]] = [{"type": "text", "text": task}]
    b64 = getattr(observation, "screenshot_b64", None)
    if b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
                "screen_seq": getattr(observation, "screen_seq", 0),
            }
        )
    digest = getattr(observation, "current_app", None)
    if digest:
        content.append({"type": "text", "text": f"[OBS] app={digest}"})
    return content


class ThinPhoneAgent:
    """Thin-loop phone agent built on ``create_agent`` + v2 middleware."""

    def __init__(self, config: Any, checkpointer: Any | None = None) -> None:
        self.config = config
        self.run_id = uuid.uuid4().hex

        # Lazy imports: these modules are produced by the concurrent core/tools
        # worktrees and may not exist when this module is first imported.
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware

        from phone_agent.v2.model import build_chat_model
        from phone_agent.v2.session import PhoneSession
        from phone_agent.v2.tools import build_tools
        from phone_agent.v2.middleware.images import build_image_middleware
        from phone_agent.v2.middleware.safety import build_hitl_middleware
        from phone_agent.v2.middleware.trace import build_trace_middleware

        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()
        self.checkpointer = checkpointer

        self.session = PhoneSession(config)
        self.model = build_chat_model(config)
        self.tools = build_tools(self.session, config)

        self._trace = build_trace_middleware(
            self.run_id,
            trace_dir=getattr(config, "trace_dir", ".traces"),
            enabled=getattr(config, "trace_enabled", True),
        )
        self.trace_path = self._trace.trace_path

        middleware = [
            build_hitl_middleware(self.session),
            build_image_middleware(),
            self._trace,
            ModelCallLimitMiddleware(
                thread_limit=getattr(config, "max_model_calls", 20),
                exit_behavior="end",
            ),
        ]

        # TaskDoc render/nudge middleware (task-board increment). Guarded so a
        # missing taskdoc module (e.g. this file imported before the concurrent
        # W1 worktree lands) degrades gracefully to a plain thin loop.
        if getattr(config, "taskdoc_enabled", True):
            try:
                from phone_agent.v2.middleware.taskdoc import build_taskdoc_middleware

                middleware.append(
                    build_taskdoc_middleware(
                        self.session,
                        lang=getattr(config, "lang", "cn"),
                        nudge_steps=getattr(config, "taskdoc_nudge_steps", 5),
                    )
                )
            except Exception:  # noqa: BLE001 - optional increment; never block bring-up
                pass

        # Diagnostic evidence stream (live-diagnosis skill). Appended LAST so its
        # before_model sees the post-image-prune + post-TaskDoc context and its
        # wrap_tool_call is innermost (raw tool return). Default OFF, zero-cost;
        # guarded like taskdoc so a missing module degrades to a plain thin loop.
        self._diagnostic = None
        if getattr(config, "diagnostic_evidence", False):
            try:
                from phone_agent.v2.middleware.diagnostic import (
                    build_diagnostic_middleware,
                )

                self._diagnostic = build_diagnostic_middleware(
                    run_id=self.run_id,
                    evidence_dir=getattr(
                        config,
                        "diagnostic_evidence_dir",
                        "outputs/live-diagnosis/.evidence",
                    ),
                    session=self.session,
                    enabled=True,
                )
                middleware.append(self._diagnostic)
            except Exception:  # noqa: BLE001 - optional increment; never block bring-up
                self._diagnostic = None
        self.evidence_path = getattr(self._diagnostic, "evidence_path", None)

        from phone_agent.v2.prompts import get_system_prompt

        self.agent = create_agent(
            self.model,
            tools=self.tools,
            middleware=middleware,
            checkpointer=checkpointer,
        )
        self._system_prompt = get_system_prompt(getattr(config, "lang", "cn"))

    # ------------------------------------------------------------------
    def _initial_messages(self, task: str) -> list[Any]:
        try:
            observation = self.session.observe()
            content = _first_observation_content(observation, task)
        except Exception:
            # Observation failure -> text-only start (fail-open on bring-up).
            content = [{"type": "text", "text": task}]
        return [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=content),
        ]

    @staticmethod
    def _extract_interrupts(result: Any) -> tuple[Any, ...]:
        if isinstance(result, dict) and "__interrupt__" in result:
            return tuple(result["__interrupt__"])
        interrupts = getattr(result, "interrupts", None)
        if interrupts:
            return tuple(interrupts)
        return ()

    @staticmethod
    def _decisions_for(interrupt: Any, hitl_handler: Callable[[str], str]) -> list[dict[str, Any]]:
        """Map one HITL interrupt into a decisions list for ``Command(resume=...)``.

        The interrupt value is a ``HITLRequest`` with ``action_requests`` and
        ``review_configs``. For each action we ask the handler and translate its
        free-form answer into an approve/reject/respond decision.
        """
        value = getattr(interrupt, "value", interrupt)
        action_requests = []
        review_configs = []
        if isinstance(value, dict):
            action_requests = value.get("action_requests", []) or []
            review_configs = value.get("review_configs", []) or []

        decisions: list[dict[str, Any]] = []
        for idx, action in enumerate(action_requests):
            allowed = ["approve", "reject"]
            if idx < len(review_configs):
                allowed = review_configs[idx].get("allowed_decisions", allowed)
            name = action.get("name", "") if isinstance(action, dict) else ""
            description = action.get("description", name) if isinstance(action, dict) else name
            answer = str(hitl_handler(str(description))).strip().lower()

            if "respond" in allowed:
                decisions.append({"type": "respond", "message": str(answer)})
            elif answer in {"approve", "yes", "y", "同意", "确认", "ok"}:
                decisions.append({"type": "approve"})
            else:
                decisions.append({"type": "reject", "message": answer or "rejected"})
        return decisions

    # ------------------------------------------------------------------
    def _seed_task_doc(self, task: str) -> None:
        """Harness-seed the task board's goal base at run start (§2.5).

        ``goal_base`` is only ever set here — the model can never rewrite it (it
        writes exclusively through ``update_task_doc``). Lazy/guarded import so a
        missing taskdoc module degrades to a plain thin loop.
        """

        if not getattr(self.config, "taskdoc_enabled", True):
            return
        try:
            from phone_agent.v2.taskdoc import TaskDoc

            self.session.task_doc = TaskDoc(goal_base=task)
        except Exception:  # noqa: BLE001 - optional increment; skip seeding on import failure
            pass

    # ------------------------------------------------------------------
    def run(self, task: str, hitl_handler: Callable[[str], str] = input) -> RunResult:
        from langgraph.types import Command

        self._seed_task_doc(task)

        config = {"configurable": {"thread_id": self.run_id}}
        payload: Any = {"messages": self._initial_messages(task)}

        result: Any = None
        # Bound the resume loop to the model-call budget plus HITL slack.
        max_rounds = getattr(self.config, "max_model_calls", 20) + 5
        for _ in range(max_rounds):
            result = self.agent.invoke(payload, config)
            interrupts = self._extract_interrupts(result)
            if not interrupts:
                break
            decisions: list[dict[str, Any]] = []
            for interrupt in interrupts:
                decisions.extend(self._decisions_for(interrupt, hitl_handler))
            payload = Command(resume={"decisions": decisions})

        return self._build_result(result)

    def _build_result(self, result: Any) -> RunResult:
        session = self.session
        steps = self._trace._step
        finished = bool(getattr(session, "finished", False))
        takeover_reason = getattr(session, "takeover_reason", None)

        if takeover_reason:
            return RunResult(False, str(takeover_reason), steps, self.trace_path)
        if finished:
            summary = getattr(session, "finish_summary", None) or "finished"
            return RunResult(True, str(summary), steps, self.trace_path)

        # No terminal declaration: distinguish budget exhaustion from a model
        # that simply stopped emitting tool calls.
        reason = "model_stopped"
        if isinstance(result, dict):
            messages = result.get("messages") or []
            for message in reversed(messages):
                content = getattr(message, "content", "")
                if isinstance(content, str) and "limit" in content.lower():
                    reason = "max_model_calls"
                    break
        return RunResult(False, reason, steps, self.trace_path)


__all__ = ["ThinPhoneAgent", "RunResult"]
