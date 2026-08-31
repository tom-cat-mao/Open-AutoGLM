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
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from phone_agent.v2.capabilities import (
    CapabilityAssemblyContext,
    MiddlewareReplacement,
    PromptBlock,
    assemble_capabilities,
    build_capability_registry,
)


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
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.run_id = str(run_id or uuid.uuid4().hex)
        self.capability_registry = build_capability_registry(config)
        self._run_capabilities: dict[str, str] = {}
        self._run_memory_generation: dict[str, Any] | None = None
        self._run_capability_snapshot_ready = False

        # Lazy imports: these modules are produced by the concurrent core/tools
        # worktrees and may not exist when this module is first imported.
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware

        from phone_agent.v2.model import build_chat_model
        from phone_agent.v2.session import PhoneSession
        tools_module = sys.modules.get("phone_agent.v2.tools")
        if tools_module is None:
            from phone_agent.v2 import tools as tools_module
        from phone_agent.v2.usage import UsageLedger
        from phone_agent.v2.middleware.budget import build_budget_middleware
        from phone_agent.v2.middleware.images import build_context_pruning_middleware
        from phone_agent.v2.middleware.safety import (
            build_capability_safety_middleware,
            build_control_hitl_middleware,
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
        build_base_tools = getattr(tools_module, "build_base_tools", None)
        native_tool_assembly = callable(build_base_tools)

        # Observe-only experience sink.  The experience capability opens it at
        # run start and attaches it to trace before any model/tool call.
        self._experience_writer = None
        self._trace = build_trace_middleware(
            self.run_id,
            trace_dir=getattr(config, "trace_dir", ".traces"),
            enabled=getattr(config, "trace_enabled", True),
            experience_writer=self._experience_writer,
            session=self.session,
        )
        self.trace_path = self._trace.trace_path

        def taskdoc_middleware_factory():
            from phone_agent.v2.middleware.taskdoc import build_taskdoc_middleware

            return build_taskdoc_middleware(
                self.session,
                lang=getattr(config, "lang", "cn"),
                nudge_steps=getattr(config, "taskdoc_nudge_steps", 5),
            )

        def compact_middleware_factory():
            from phone_agent.v2.middleware.compact import build_compact_middleware

            return build_compact_middleware(
                self.session,
                config,
                model=self.model,
                memory_state_provider=self._compact_memory_state,
            )

        def budget_middleware_factory():
            return build_budget_middleware(
                token_budget=getattr(config, "token_budget", 1_000_000),
                warn_remaining=getattr(config, "token_warn_remaining", 100_000),
                lang=getattr(config, "lang", "cn"),
                ledger=self.usage_ledger,
                trace_recorder=getattr(self._trace, "record_event", None),
            )

        def taskdoc_tool_factory():
            if not native_tool_assembly:
                return None
            from phone_agent.v2.tools.taskdoc import make_update_task_doc_tool

            return make_update_task_doc_tool(
                self.session, getattr(config, "lang", "cn")
            )

        def finish_verify_tool_factory():
            if not native_tool_assembly:
                return None
            from phone_agent.v2.tools.control import make_finish_tool

            return make_finish_tool(self.session, config)

        self._capability_ctx = CapabilityAssemblyContext(
            {
                "taskdoc_middleware_factory": taskdoc_middleware_factory,
                "taskdoc_tool_factory": taskdoc_tool_factory,
                "taskdoc_run_start": self._taskdoc_run_start,
                "safety_middleware_factory": lambda: (
                    MiddlewareReplacement(
                        build_capability_safety_middleware(self.session, config),
                        "control_hitl",
                    )
                    if getattr(config, "safety_mode", "wary") == "hard"
                    else build_capability_safety_middleware(self.session, config)
                ),
                "budget_middleware_factory": budget_middleware_factory,
                "compact_middleware_factory": compact_middleware_factory,
                "finish_verify_tool_factory": finish_verify_tool_factory,
                "app_kb_run_start": self._app_kb_run_start,
                "app_kb_prompt_provider": self._app_kb_prompt_block,
                "dream_run_end": self._dream_run_end,
                "experience_run_start": self._experience_run_start,
                "experience_run_end": self._experience_run_end,
                "recall_run_start": self._recall_run_start,
                "recall_run_end": self._recall_run_end,
                "recall_prompt_provider": self._recall_prompt_block,
            }
        )

        # Core harness products keep their pre-WP-C2 order in the gaps between
        # capability slots.  The legacy public builder is deliberately retained
        # as the baseline for integrations that replace the module wholesale;
        # same-named capability tools replace entries in place.
        base_tools = (
            build_base_tools(self.session, config)
            if native_tool_assembly
            else tools_module.build_tools(self.session, config)
        )
        for index, tool in enumerate(base_tools):
            self._capability_ctx.register_core_tool(tool, order=index)

        self._capability_ctx.register_core_middleware(
            build_control_hitl_middleware(),
            order=0,
            replace_key="control_hitl",
        )
        self._capability_ctx.register_core_middleware(
            build_context_pruning_middleware(
                keep_images=getattr(config, "image_keep", 2),
                keep_marks=getattr(config, "obs_marks_keep", 2),
            ),
            order=30,
        )
        self._capability_ctx.register_core_middleware(self._trace, order=50)
        self._capability_ctx.register_core_middleware(
            ModelCallLimitMiddleware(
                thread_limit=getattr(config, "max_model_calls", 100),
                exit_behavior="end",
            ),
            order=60,
        )
        self._capability_ctx.add_core_run_hook(
            "start", self._capability_snapshot_run_start, order=30
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
                self._capability_ctx.register_core_middleware(
                    self._diagnostic, order=70
                )
            except Exception:  # noqa: BLE001 - optional increment; never block bring-up
                self._diagnostic = None
        self.evidence_path = getattr(self._diagnostic, "evidence_path", None)

        # Optional add-ons may observe the run without coupling the core to a UI
        # or transport. Place them outside the final safety wrapper so a blocked
        # warning result remains visible to observers.
        for index, observer in enumerate(extra_middleware or []):
            self._capability_ctx.register_core_middleware(
                observer, order=80 + index
            )

        assemble_capabilities(self.capability_registry, self._capability_ctx)
        self.tools = self._capability_ctx.tools
        middleware = self._capability_ctx.middleware
        self._budget = self._owned_capability_product("budget", "middleware")
        self._compact = self._owned_capability_product("compact", "middleware")
        safety_product = self._owned_capability_product("safety", "middleware")
        self._safety_warning = (
            safety_product
            if safety_product is not None
            and hasattr(safety_product, "warning_count")
            else None
        )

        from phone_agent.v2.prompts import get_system_prompt

        self.agent = create_agent(
            self.model,
            tools=self.tools,
            middleware=middleware,
            checkpointer=checkpointer,
        )
        self._base_system_prompt = get_system_prompt(getattr(config, "lang", "cn"))
        self._system_prompt = self._base_system_prompt
        self._revoked_lesson_ids: set[str] = set()

    def _owned_capability_product(self, cap_id: str, seam: str) -> Any | None:
        values = self._capability_ctx.owned_values(cap_id, seam)
        return values[0] if values else None

    # ------------------------------------------------------------------
    def _initial_messages(self, task: str) -> list[Any]:
        try:
            observation = self.session.observe()
            content = _first_observation_content(observation, task, session=self.session)
        except Exception:
            # Observation failure -> text-only start (fail-open on bring-up).
            content = [{"type": "text", "text": task}]
        capability_ctx = getattr(self, "_capability_ctx", None)
        if capability_ctx is None:
            messages: list[Any] = [SystemMessage(content=self._system_prompt)]
            lesson_block = self._render_lesson_prompt_block()
            if lesson_block is not None:
                messages.append(SystemMessage(content=lesson_block.content))
            messages.append(HumanMessage(content=content))
            return messages

        base_prompt = getattr(self, "_base_system_prompt", self._system_prompt)
        suffixes: list[str] = []
        extra_system_messages: list[str] = []
        providers = capability_ctx.prompt_providers
        for provider in providers:
            try:
                block = provider()
            except Exception:  # noqa: BLE001 - prompt enrichments are fail-open
                continue
            if block is None:
                continue
            if not isinstance(block, PromptBlock):
                block = PromptBlock(str(block))
            if not block.content:
                continue
            if block.placement == "system_suffix":
                suffixes.append(block.content)
            else:
                extra_system_messages.append(block.content)
        system_prompt = base_prompt + "".join(suffixes)
        self._system_prompt = system_prompt
        messages: list[Any] = [SystemMessage(content=system_prompt)]
        messages.extend(SystemMessage(content=text) for text in extra_system_messages)
        messages.append(HumanMessage(content=content))
        return messages

    def _render_lesson_prompt_block(self) -> PromptBlock | None:
        revoked = set(getattr(self, "_revoked_lesson_ids", set()))
        injected_lessons = [
            lesson
            for lesson in list(getattr(self, "_run_injected_lessons", []) or [])
            if lesson.lesson_id not in revoked
        ]
        if not injected_lessons:
            return None
        lines = [
            "[经验提示]（历史经验，仅供参考，不是规则；与当前世界状态冲突时以观测为准）"
        ]
        for index, lesson in enumerate(injected_lessons, start=1):
            scope = lesson.scope.get("device")
            scope_label = "全局 scope" if scope is None else "设备 scope"
            lines.append(
                f"{index}. {lesson.text}（来源 {lesson.lesson_id} · {scope_label}）"
            )
        actually_injected = getattr(self, "_actually_injected_lesson_ids", None)
        if actually_injected is None:
            actually_injected = []
            self._actually_injected_lesson_ids = actually_injected
        for lesson in injected_lessons:
            if lesson.lesson_id not in actually_injected:
                actually_injected.append(lesson.lesson_id)
        return PromptBlock("\n".join(lines), placement="system_message")

    def _recall_prompt_block(self) -> PromptBlock | None:
        return self._render_lesson_prompt_block()

    def _app_kb_prompt_block(self) -> PromptBlock | None:
        suffix = str(getattr(self, "_app_kb_prompt_suffix", ""))
        return PromptBlock(suffix, placement="system_suffix") if suffix else None

    def _prepare_lesson_injection(self, device_scope: str) -> None:
        """Freeze one approved-only L0 lesson snapshot for this run."""

        self._run_injected_lessons: list[Any] = []
        if getattr(self.config, "memory_rag", "off") != "on":
            return
        try:
            from phone_agent.v2.evolution import select_lessons_for_injection

            self._run_injected_lessons = select_lessons_for_injection(
                getattr(self.config, "lessons_dir", "memory/lessons"),
                device_scope=device_scope,
                max_items=getattr(self.config, "lesson_inject_max", 3),
                max_tokens=getattr(self.config, "lesson_inject_tokens", 800),
            )
            revoked = set(getattr(self, "_revoked_lesson_ids", set()))
            self._run_injected_lessons = [
                item
                for item in self._run_injected_lessons
                if item.lesson_id not in revoked
            ]
        except Exception:  # noqa: BLE001 - optional memory injection is fail-open
            self._run_injected_lessons = []

        if not self._run_injected_lessons:
            return
        try:
            record = getattr(self._trace, "record_event", None)
            if callable(record):
                lesson_ids = [item.lesson_id for item in self._run_injected_lessons]
                record(
                    "lesson_injection",
                    lesson_ids=lesson_ids,
                    count=len(lesson_ids),
                )
        except Exception:  # noqa: BLE001 - trace failure cannot block injection/run
            pass

    def revoke_lesson(self, lesson_id: str) -> bool:
        """Emergency-revoke one lesson and exclude it from future injection.

        Already-sent model messages are immutable and remain truthful history.
        This method updates the authoritative store immediately and makes every
        later provider evaluation (including future event-triggered injection)
        ignore the id.  Damaged stores and unknown ids fail open to the run.
        """

        clean_id = str(lesson_id).strip()
        if not clean_id:
            return False
        try:
            from phone_agent.v2.evolution import emergency_revoke_lesson

            revoked = emergency_revoke_lesson(
                getattr(self.config, "lessons_dir", "memory/lessons"),
                clean_id,
            )
            if not revoked:
                return False
        except Exception:  # noqa: BLE001 - emergency control must not stop the run
            return False
        self._revoked_lesson_ids.add(clean_id)
        try:
            record = getattr(self._trace, "record_event", None)
            if callable(record):
                record("lesson_revoked", lesson_id=clean_id, source="runner_control")
        except Exception:  # noqa: BLE001 - trace is never authoritative
            pass
        return True

    def _prepare_app_knowledge(self) -> None:
        """Sync App-KB once and rebuild this run's bounded prompt suffix."""

        base_prompt = getattr(
            self, "_base_system_prompt", getattr(self, "_system_prompt", "")
        )
        self._base_system_prompt = base_prompt
        self._system_prompt = base_prompt
        self._app_kb_prompt_suffix = ""
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
                self._app_kb_prompt_suffix = (
                    "\n\n# 本机可启动应用（launch_app 请用这些名字）\n"
                    f"{app_list}"
                )
                self._system_prompt = base_prompt + self._app_kb_prompt_suffix
        except Exception:  # noqa: BLE001 - prompt enrichment never blocks a run
            self._system_prompt = base_prompt
            self._app_kb_prompt_suffix = ""

    def _taskdoc_run_start(self, state: dict[str, Any]) -> None:
        self._seed_task_doc(str(state["task"]))

    def _app_kb_run_start(self, _state: dict[str, Any]) -> None:
        self._prepare_app_knowledge()

    def _capability_snapshot_run_start(self, _state: dict[str, Any]) -> None:
        self._record_capability_snapshot()

    def _experience_run_start(self, state: dict[str, Any]) -> None:
        self._experience_writer = None
        if not getattr(self.config, "experience_enabled", False):
            return
        try:
            from phone_agent.v2.experience import ExperienceWriter

            self._experience_writer = ExperienceWriter(
                getattr(self.config, "experience_dir", "memory/experience")
            )
        except Exception:  # noqa: BLE001 - optional persistence is fail-open
            self._experience_writer = None
        trace = getattr(self, "_trace", None)
        if trace is not None:
            attach = getattr(trace, "set_experience_writer", None)
            if callable(attach):
                attach(self._experience_writer)
            else:
                trace._experience_writer = self._experience_writer
        state["device_scope"] = self._experience_device_scope()
        if trace is not None:
            trace.experience_device_scope = state["device_scope"]

    def _recall_run_start(self, state: dict[str, Any]) -> None:
        self._shadow_recall_start(str(state["task"]))
        if (
            state.get("device_scope") == "device:unknown"
            and getattr(self.config, "memory_rag", "off") == "on"
        ):
            state["device_scope"] = self._experience_device_scope()
        self._prepare_lesson_injection(str(state["device_scope"]))

    def _recall_run_end(self, _state: dict[str, Any]) -> None:
        if _state.get("exception"):
            return
        self._shadow_recall_finish()

    def _experience_run_end(self, state: dict[str, Any]) -> None:
        result = state.get("result")
        if not isinstance(result, RunResult):
            return
        self._append_experience_outcome(
            str(state["task"]),
            result,
            ts_start=float(state["ts_start"]),
            ts_end=float(state.get("ts_end", time.time())),
            device_scope=str(state.get("device_scope", "device:unknown")),
        )

    def _dream_run_end(self, _state: dict[str, Any]) -> None:
        if (
            _state.get("exception")
            or getattr(self.config, "dream_mode", "manual") != "auto"
        ):
            return
        try:
            from phone_agent.v2.dream import run_maintenance

            self._last_dream_summary = run_maintenance(
                self.config,
                light=True,
                store=getattr(self.session, "app_store", None),
                inventory_provider=self._installed_app_inventory,
            )
        except Exception as exc:  # noqa: BLE001 - maintenance never masks outcome
            self._last_dream_summary = {
                "status": "skipped",
                "reason": type(exc).__name__,
            }

    def _installed_app_inventory(self) -> set[str] | None:
        try:
            inventory = self.session.device_factory.get_installed_app_inventory(
                getattr(self.config, "device_id", None)
            )
            packages = set(getattr(inventory, "packages", ()) or ())
            return packages or None
        except Exception:  # noqa: BLE001 - dream is fail-open without a device
            return None

    def _execute_run_hooks(self, when: str, state: dict[str, Any]) -> None:
        capability_ctx = getattr(self, "_capability_ctx", None)
        if capability_ctx is None:
            return
        for hook in capability_ctx.run_hooks(when):
            try:
                hook(state)
            except Exception:  # noqa: BLE001 - capability side planes are fail-open
                continue

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
                    "Qwen/Qwen3-Embedding-0.6B",
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

    def _app_kb_generation(self) -> dict[str, Any] | None:
        """Return the App-KB generation marker without mutating the store.

        A future materialized format may expose ``generation`` or ``version``.
        The current ``kb.json`` is a list, so its nanosecond mtime is the
        explicitly documented fallback generation identifier.
        """

        store = getattr(self.session, "app_store", None)
        path = getattr(store, "kb_path", None)
        if path is None:
            path = Path(getattr(self.config, "memory_dir", "memory")) / "app_kb/kb.json"
        try:
            path = Path(path)
            mtime_ns = path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError):
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            for key in ("generation", "version"):
                if key in payload:
                    return {"source": f"kb.json.{key}", "value": payload[key]}
        return {"source": "kb.json.mtime_ns", "value": mtime_ns}

    def _record_capability_snapshot(self) -> None:
        """Freeze one run-start composition for trace and episode reuse."""

        try:
            statuses = self.capability_registry.status()
            self._run_capabilities = {
                str(row["cap_id"]): str(row["state"]) for row in statuses
            }
            self._run_memory_generation = self._app_kb_generation()
            self._run_capability_snapshot_ready = True
        except Exception:  # noqa: BLE001 - architecture telemetry is fail-open
            self._run_capabilities = {}
            self._run_memory_generation = None
            self._run_capability_snapshot_ready = False
            return
        try:
            record = getattr(self._trace, "record_event", None)
            if callable(record):
                record(
                    "capability_snapshot",
                    capabilities=statuses,
                    memory_generation=self._run_memory_generation,
                )
        except Exception:  # noqa: BLE001 - trace failure must not erase episode data
            pass

    def _compact_memory_state(self) -> dict[str, Any] | None:
        """Expose the run snapshot used by compact's deterministic suffix."""

        capabilities = getattr(self, "_run_capabilities", None)
        if not getattr(self, "_run_capability_snapshot_ready", False) or not isinstance(
            capabilities, dict
        ):
            return None
        candidate_ids: list[str] = []
        for candidate in getattr(self, "_shadow_candidates", []) or []:
            if not isinstance(candidate, dict):
                continue
            ref_id = candidate.get("ref_id", candidate.get("id"))
            if ref_id is not None:
                candidate_ids.append(str(ref_id))
        return {
            "capabilities": dict(capabilities),
            "memory_generation": getattr(
                self, "_run_memory_generation", None
            ),
            "shadow_candidate_ids": candidate_ids,
        }

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
        reset_implicit_alias = getattr(
            self.session, "reset_implicit_alias_state", None
        )
        if callable(reset_implicit_alias):
            reset_implicit_alias(self.run_id)
        self._actually_injected_lesson_ids = []
        run_state: dict[str, Any] = {
            "task": task,
            "ts_start": ts_start,
            "device_scope": "device:unknown",
        }
        capability_ctx = getattr(self, "_capability_ctx", None)
        if capability_ctx is not None:
            # Clear run-scoped capability state before active hooks rebuild it.
            # This is also what makes a prior apply -> release leave no semantic
            # residue when the same process assembles another run.
            try:
                self.session.task_doc = None
            except Exception:  # noqa: BLE001 - duck-typed sessions may be immutable
                pass
            self._shadow_candidates = []
            self._shadow_recall_ready = False
            self._run_injected_lessons = []
            self._app_kb_prompt_suffix = ""
            self._last_dream_summary = None
            # A reused process may have reconciled experience off since the
            # previous run. Clear the prior sink before active start hooks mount
            # the current writer, so disabled means no residual persistence.
            self._experience_writer = None
            if getattr(self, "_trace", None) is not None:
                attach = getattr(self._trace, "set_experience_writer", None)
                if callable(attach):
                    attach(None)
                else:
                    self._trace._experience_writer = None
            self._execute_run_hooks("start", run_state)
        else:
            # Compatibility for unit-test doubles constructed via ``__new__``.
            self._seed_task_doc(task)
            self._prepare_app_knowledge()
            self._record_capability_snapshot()
            self._shadow_recall_start(task)
            if (
                getattr(self, "_experience_writer", None) is not None
                or getattr(self.config, "memory_rag", "off") == "on"
            ):
                run_state["device_scope"] = self._experience_device_scope()
            self._prepare_lesson_injection(str(run_state["device_scope"]))
        device_scope = str(run_state["device_scope"])
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
            run_state.update(result=failed, ts_end=time.time(), exception=True)
            if capability_ctx is not None:
                self._execute_run_hooks("end", run_state)
            else:
                self._append_experience_outcome(
                    task,
                    failed,
                    ts_start=ts_start,
                    ts_end=run_state["ts_end"],
                    device_scope=device_scope,
                )
            raise

        run_result = self._build_result(result)
        run_state.update(result=run_result, ts_end=time.time())
        if capability_ctx is not None:
            self._execute_run_hooks("end", run_state)
        else:
            self._shadow_recall_finish()
            self._append_experience_outcome(
                task,
                run_result,
                ts_start=ts_start,
                ts_end=run_state["ts_end"],
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
                capabilities=dict(getattr(self, "_run_capabilities", {})),
                injected_lessons=[
                    lesson_id
                    for lesson_id in getattr(
                        self, "_actually_injected_lesson_ids", []
                    )
                ],
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
