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
from datetime import datetime
import time
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage


@dataclass
class RunResult:
    """Outcome of a single :meth:`ThinPhoneAgent.run` invocation (§10)."""

    success: bool
    reason: str
    steps: int
    trace_path: str | None = None


def _marks_digest_lines(marks: Any, max_items: int = 40) -> str:
    """Render ``mark_id | role | text | center`` lines (mirrors session digest).

    Used only as a fallback when the session does not expose
    ``format_marks_digest`` (e.g. duck-typed test doubles).
    """

    items = list(marks.values()) if isinstance(marks, dict) else list(marks or [])
    lines: list[str] = []
    for mark in items[:max_items]:
        role = (getattr(mark, "role", None) or "?")[:24]
        text = (getattr(mark, "text_summary", None) or "").replace("\n", " ").strip()[:32]
        center = tuple(getattr(mark, "center", None) or ())
        mark_id = getattr(mark, "mark_id", "?")
        lines.append(f"{mark_id} | {role} | {text} | {center}")
    if len(items) > max_items:
        lines.append(f"... (+{len(items) - max_items} more)")
    return "\n".join(lines)


def _first_observation_content(
    observation: Any, task: str, session: Any = None
) -> list[dict[str, Any]]:
    """Build the initial user message: task text + screenshot + marks digest.

    The ``[OBS]`` text block mirrors the tool-path observation shape
    (``tools/_obs.py``: ``app=... screen#...`` + a marks digest) so the model
    sees the current screen's marks — and can address ``target_mark_id`` —
    from the very first step, instead of having to burn a ``read_screen``.
    """

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
    marks = getattr(observation, "marks", None) or []
    items = list(marks.values()) if isinstance(marks, dict) else list(marks)
    digest_fn = getattr(session, "format_marks_digest", None)
    digest = digest_fn(items) if callable(digest_fn) else _marks_digest_lines(items)
    current_app = getattr(observation, "current_app", None) or "?"
    seq = getattr(observation, "screen_seq", 0)
    content.append(
        {
            "type": "text",
            "text": f"[OBS] app={current_app} screen#{seq}\nmarks ({len(items)}): {digest}",
        }
    )
    return content


class ThinPhoneAgent:
    """Thin-loop phone agent built on ``create_agent`` + v2 middleware.

    ``extra_middleware`` is an optional observability extension point for
    add-ons such as the local web UI. Extra middleware is appended to the
    built-in observation stack, before the safety-warning wrapper, so it can
    observe both ordinary tool results and calls short-circuited by safety.
    The core agent remains fully headless when the argument is omitted.
    """

    def __init__(
        self,
        config: Any,
        checkpointer: Any | None = None,
        extra_middleware: list[Any] | None = None,
    ) -> None:
        self.config = config
        self.run_id = uuid.uuid4().hex

        # Lazy imports: these modules are produced by the concurrent core/tools
        # worktrees and may not exist when this module is first imported.
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware

        from phone_agent.v2.model import build_chat_model
        from phone_agent.v2.session import PhoneSession
        from phone_agent.v2.tools import build_tools
        from phone_agent.v2.usage import UsageLedger
        from phone_agent.v2.middleware.budget import build_budget_middleware
        from phone_agent.v2.middleware.images import build_context_pruning_middleware
        from phone_agent.v2.middleware.safety import (
            build_hitl_middleware,
            build_safety_warning_middleware,
        )
        from phone_agent.v2.middleware.trace import build_trace_middleware

        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()
        self.checkpointer = checkpointer

        self.session = PhoneSession(config)
        self.usage_ledger = UsageLedger()
        self.session.usage_ledger = self.usage_ledger
        self.model = build_chat_model(config)
        self.tools = build_tools(self.session, config)

        # Observe-only experience sink. Construction and every write are
        # fail-open: local memory must never change actor behavior or run outcome.
        self._experience_writer = None
        if getattr(config, "experience_enabled", False):
            try:
                from phone_agent.v2.experience import ExperienceWriter

                self._experience_writer = ExperienceWriter(
                    getattr(config, "experience_dir", "memory/experience")
                )
            except Exception:  # noqa: BLE001 - optional local persistence
                self._experience_writer = None

        self._trace = build_trace_middleware(
            self.run_id,
            trace_dir=getattr(config, "trace_dir", ".traces"),
            enabled=getattr(config, "trace_enabled", True),
            experience_writer=self._experience_writer,
            session=self.session,
        )
        self.trace_path = self._trace.trace_path

        # L0 token-budget mirror + hard cost ceiling (A4 §2). Warns once as the
        # remaining token budget crosses the line, and hard-stops the run when the
        # budget is fully spent. Held as an attribute so run() can reset its
        # per-run state on reuse (S1 R7) and _build_result can read ``exhausted``.
        self._budget = build_budget_middleware(
            token_budget=getattr(config, "token_budget", 1_000_000),
            warn_remaining=getattr(config, "token_warn_remaining", 100_000),
            lang=getattr(config, "lang", "cn"),
            ledger=self.usage_ledger,
        )

        # Two-threshold auto-compact (A4 §3): T1 warn + T2 forced handoff summary.
        # Guarded/optional so a missing module or disabled switch degrades to a
        # plain thin loop. Placed before context-pruning (coarse fold before the
        # fine-grained image/marks prune).
        self._compact = None
        if getattr(config, "compact_enabled", True):
            try:
                from phone_agent.v2.middleware.compact import build_compact_middleware

                self._compact = build_compact_middleware(
                    self.session, config, model=self.model
                )
            except Exception:  # noqa: BLE001 - optional increment; never block bring-up
                self._compact = None

        middleware = [
            build_hitl_middleware(self.session, config),
        ]
        # TaskDoc render + flow-line middleware (task-board increment). Guarded so
        # a missing taskdoc module (e.g. this file imported before the concurrent
        # W1 worktree lands) degrades gracefully to a plain thin loop. Kept before
        # compact so the pinned [TASK_DOC] block exists when compact chooses its
        # cut point (compact never folds the pinned block). ``nudge_steps`` is a
        # deprecated no-op kwarg (U3 removed the stagnation nudge) kept for
        # backward-compatible construction.
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

        if self._compact is not None:
            middleware.append(self._compact)

        middleware.extend(
            [
                build_context_pruning_middleware(
                    keep_images=getattr(config, "image_keep", 2),
                    keep_marks=getattr(config, "obs_marks_keep", 2),
                ),
                self._budget,
                self._trace,
                ModelCallLimitMiddleware(
                    thread_limit=getattr(config, "max_model_calls", 100),
                    exit_behavior="end",
                ),
            ]
        )

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
                    unredacted=bool(getattr(config, "diagnostic_unredacted", False)),
                )
                middleware.append(self._diagnostic)
            except Exception:  # noqa: BLE001 - optional increment; never block bring-up
                self._diagnostic = None
        self.evidence_path = getattr(self._diagnostic, "evidence_path", None)

        # Optional add-ons may observe the run without coupling the core to a UI
        # or transport. Place them outside the final safety wrapper so a blocked
        # warning result remains visible to observers.
        middleware.extend(list(extra_middleware or []))

        # Safety warning flow (U2). In wary/reviewer mode a risky execution call
        # is short-circuited with a warning ToolMessage instead of being executed
        # or human-interrupted; the model resends with confirm_irreversible=true.
        # Appended LAST so it is the innermost wrap_tool_call — trace + diagnostic
        # (outer) still record the blocked call as the tool result. Returns None in
        # off/hard mode (hard mode uses the HITL interrupt instead).
        self._safety_warning = build_safety_warning_middleware(self.session, config)
        if self._safety_warning is not None:
            middleware.append(self._safety_warning)

        from phone_agent.v2.prompts import get_system_prompt

        self.agent = create_agent(
            self.model,
            tools=self.tools,
            middleware=middleware,
            checkpointer=checkpointer,
        )
        self._base_system_prompt = get_system_prompt(getattr(config, "lang", "cn"))
        self._system_prompt = self._base_system_prompt

    # ------------------------------------------------------------------
    def _initial_messages(self, task: str) -> list[Any]:
        try:
            observation = self.session.observe()
            content = _first_observation_content(observation, task, session=self.session)
        except Exception:
            # Observation failure -> text-only start (fail-open on bring-up).
            content = [{"type": "text", "text": task}]
        return [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=content),
        ]

    def _prepare_app_knowledge(self) -> None:
        """Sync App-KB once and rebuild this run's bounded prompt suffix."""

        base_prompt = getattr(
            self, "_base_system_prompt", getattr(self, "_system_prompt", "")
        )
        self._base_system_prompt = base_prompt
        self._system_prompt = base_prompt
        if not getattr(self.config, "app_kb_enabled", True):
            return
        try:
            sync = getattr(self.session, "sync_app_knowledge", None)
            if callable(sync):
                sync()
            render = getattr(self.session, "app_list_for_prompt", None)
            app_list = (
                render(getattr(self.config, "app_list_max", 40))
                if callable(render)
                else ""
            )
            if app_list:
                self._system_prompt += (
                    "\n\n# 本机可启动应用（launch_app 请用这些名字）\n"
                    f"{app_list}"
                )
        except Exception:  # noqa: BLE001 - prompt enrichment never blocks a run
            self._system_prompt = base_prompt

    def _shadow_recall_start(self, task: str) -> None:
        """Retrieve trace-only candidates without touching actor context."""

        self._shadow_candidates: list[dict[str, Any]] = []
        self._shadow_recall_ready = False
        reset = getattr(self._trace, "reset_run_observations", None)
        if callable(reset):
            reset()
        if getattr(self.config, "memory_rag", "off") != "shadow":
            return

        trace_payload: dict[str, Any] = {"mode": "shadow", "candidates": []}
        try:
            from phone_agent.v2.recall import MlxEmbedder, VecIndex

            serial = getattr(self.config, "device_id", None)
            if not serial:
                serial_getter = getattr(self.session, "_kb_device_id", None)
                serial = serial_getter() if callable(serial_getter) else None
            if not serial:
                trace_payload["status"] = "skipped"
                trace_payload["reason"] = "device_scope_unavailable"
            else:
                model_id = getattr(
                    self.config,
                    "embed_model",
                    "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ",
                )
                embed_dim = getattr(self.config, "embed_dim", 1024)
                embedder = getattr(self, "_recall_embedder", None)
                if (
                    embedder is None
                    or embedder.model_id != model_id
                    or embedder.dimension != embed_dim
                ):
                    embedder = MlxEmbedder(model_id, embed_dim)
                    self._recall_embedder = embedder
                with VecIndex(
                    getattr(self.config, "vec_db", "memory/vec.db"),
                    embedder=embedder,
                ) as index:
                    self._shadow_candidates = index.recall(
                        task,
                        device_scope=f"device:{serial}",
                        top_k=getattr(self.config, "recall_top_k", 5),
                        min_score=getattr(self.config, "recall_min_score", 0.35),
                        decay_lambda=getattr(
                            self.config, "recall_decay_lambda", 0.02
                        ),
                    )
                self._shadow_recall_ready = True
                trace_payload["status"] = "ok"
                trace_payload["candidates"] = [
                    {
                        "ref_id": candidate["ref_id"],
                        "score": candidate["score"],
                        "match_reasons": candidate["match_reasons"],
                    }
                    for candidate in self._shadow_candidates
                ]
        except Exception as exc:  # noqa: BLE001 - optional shadow path is fail-open
            trace_payload["status"] = "error"
            trace_payload["error"] = type(exc).__name__

        record = getattr(self._trace, "record_event", None)
        if callable(record):
            record("run_start", memory_rag=trace_payload)

    def _shadow_recall_finish(self) -> None:
        """Evaluate trace-only recall against confirmed launch receipts."""

        if not getattr(self, "_shadow_recall_ready", False):
            return
        try:
            from pathlib import Path

            from phone_agent.v2.recall import evaluate_recall, update_recall_stats

            actual_apps = getattr(self._trace, "launched_apps", set())
            evaluation = evaluate_recall(self._shadow_candidates, actual_apps)
            stats_path = (
                Path(getattr(self.config, "memory_dir", "memory"))
                / "experience/recall_stats.json"
            )
            stats = update_recall_stats(stats_path, evaluation, run_id=self.run_id)
            record = getattr(self._trace, "record_event", None)
            if callable(record):
                record(
                    "recall_evaluation",
                    evaluation=evaluation,
                    cumulative={
                        "evaluations": stats["evaluations"],
                        "hit_rate": stats["hit_rate"],
                        "false_hit_rate": stats["false_hit_rate"],
                    },
                )
        except Exception:  # noqa: BLE001 - shadow evaluation never changes run outcome
            pass

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

        ts_start = time.time()
        self._seed_task_doc(task)
        self._prepare_app_knowledge()
        self._shadow_recall_start(task)
        # Reset per-run one-shot flags so a reused agent behaves like a fresh run
        # (S1 R7): the HITL-exhaustion terminal flag, the token-budget state, and
        # the compaction middleware's per-run counters.
        self._hitl_exhausted = False
        usage_ledger = getattr(self, "usage_ledger", None)
        if usage_ledger is not None:
            usage_ledger.reset()
        if getattr(self, "_budget", None) is not None:
            self._budget.reset()
        if getattr(self, "_compact", None) is not None:
            try:
                self._compact.reset()
            except Exception:  # noqa: BLE001 - best-effort reset; never block a run
                pass
        try:
            self.session.launched_apps = []
            self.session.finish_verifier = "skipped"
        except Exception:  # noqa: BLE001 - duck-typed sessions may be immutable
            pass
        if getattr(self, "_safety_warning", None) is not None:
            self._safety_warning.warning_count = 0

        device_scope = "device:unknown"
        if getattr(self, "_experience_writer", None) is not None:
            device_scope = self._experience_device_scope()
        if (
            getattr(self, "_experience_writer", None) is not None
            and getattr(self, "_trace", None) is not None
        ):
            self._trace.experience_device_scope = device_scope

        config = {"configurable": {"thread_id": self.run_id}}
        payload: Any = {"messages": self._initial_messages(task)}

        result: Any = None
        # The outer loop only iterates past the first invoke when a HITL interrupt
        # is raised (S1 F7); bound it by the HITL-resume budget, orthogonal to the
        # per-invoke model-call budget. ``+1`` accounts for the initial invoke.
        max_resumes = getattr(self.config, "max_hitl_resumes", 20)
        try:
            for attempt in range(max_resumes + 1):
                result = self.agent.invoke(payload, config)
                interrupts = self._extract_interrupts(result)
                if not interrupts:
                    break
                if attempt == max_resumes:
                    # Still interrupting but the resume budget is spent: terminate.
                    self._hitl_exhausted = True
                    break
                decisions: list[dict[str, Any]] = []
                for interrupt in interrupts:
                    decisions.extend(self._decisions_for(interrupt, hitl_handler))
                payload = Command(resume={"decisions": decisions})
        except Exception as exc:
            # Preserve the existing exception behavior while still closing the
            # observe-only episode. The exception text itself is never stored.
            failed = RunResult(
                False,
                f"error:{type(exc).__name__}",
                getattr(self._trace, "_step", 0),
                self.trace_path,
            )
            self._append_experience_outcome(
                task,
                failed,
                ts_start=ts_start,
                ts_end=time.time(),
                device_scope=device_scope,
            )
            raise

        run_result = self._build_result(result)
        self._shadow_recall_finish()
        self._append_experience_outcome(
            task,
            run_result,
            ts_start=ts_start,
            ts_end=time.time(),
            device_scope=device_scope,
        )
        return run_result

    def _experience_device_scope(self) -> str:
        """Resolve the allowed device namespace without exposing other state."""

        serial = getattr(self.config, "device_id", None)
        if not serial:
            getter = getattr(self.session, "_kb_device_id", None)
            if callable(getter):
                try:
                    serial = getter()
                except Exception:  # noqa: BLE001 - experience is fail-open
                    serial = None
        return f"device:{serial or 'unknown'}"

    def _append_experience_outcome(
        self,
        task: str,
        result: RunResult,
        *,
        ts_start: float,
        ts_end: float,
        device_scope: str,
    ) -> None:
        """Persist the fixed WP-I1/WP-I2 EpisodeOutcome after result building."""

        writer = getattr(self, "_experience_writer", None)
        if writer is None:
            return
        try:
            local_start = datetime.fromtimestamp(ts_start).astimezone()
            hour = local_start.hour
            if hour < 6:
                time_of_day = "night"
            elif hour < 12:
                time_of_day = "morning"
            elif hour < 18:
                time_of_day = "afternoon"
            else:
                time_of_day = "evening"

            ledger = getattr(self, "usage_ledger", None)
            tokens_total = ledger.total if ledger is not None else 0
            tokens_by_role = ledger.by_role() if ledger is not None else {}
            launched = list(getattr(self.session, "launched_apps", []) or [])
            apps = list(dict.fromkeys(str(package) for package in launched if package))
            takeover = getattr(self.session, "takeover_reason", None)
            warnings = int(
                getattr(getattr(self, "_safety_warning", None), "warning_count", 0)
            )
            writer.append_outcome(
                type="episode_outcome",
                schema_v=1,
                run_id=self.run_id,
                ts_start=ts_start,
                ts_end=ts_end,
                time_of_day=time_of_day,
                day_of_week=local_start.weekday(),
                device_scope=device_scope,
                goal_text=task,
                apps=apps,
                success=result.success,
                reason=(
                    "finished"
                    if result.success
                    else "takeover"
                    if takeover
                    else result.reason
                ),
                steps=result.steps,
                tokens_total=tokens_total,
                tokens_by_role=tokens_by_role,
                warnings=warnings,
                takeover=takeover,
                verifier=getattr(self.session, "finish_verifier", "skipped"),
            )
        except Exception:  # noqa: BLE001 - persistence cannot alter run semantics
            return

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

        # No terminal declaration. Distinguish the terminal causes (A4 §2): a
        # spent HITL-resume budget, an exhausted **token** cost budget (the L0
        # BudgetMiddleware hard ceiling set ``exhausted``), the runaway-loop fuse
        # (ModelCallLimit hard-stopped at ``max_model_calls`` — its injected
        # terminal message never runs wrap_model_call, so ``_trace._step`` equals
        # the fuse limit, F6), or a model that simply stopped emitting tool calls.
        if getattr(self, "_hitl_exhausted", False):
            return RunResult(False, "hitl_resume_exhausted", steps, self.trace_path)
        budget = getattr(self, "_budget", None)
        if budget is not None and getattr(budget, "exhausted", False):
            return RunResult(False, "token_budget_exhausted", steps, self.trace_path)
        fuse = getattr(self.config, "max_model_calls", 100)
        reason = "loop_fuse" if steps >= fuse else "model_stopped"
        return RunResult(False, reason, steps, self.trace_path)


__all__ = ["ThinPhoneAgent", "RunResult"]
