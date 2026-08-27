"""Main PhoneAgent class for orchestrating phone automation."""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from phone_agent.checkpoint import build_hitl_checkpointer
from phone_agent.device_factory import get_device_factory
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.graph.builder import create_agent_graph
from phone_agent.config import PROMPT_VERSION, get_prompt_version
from phone_agent.graph.context import (
    DEFAULT_CONTEXT_MODE,
    build_context_metrics,
    default_context_budget,
    default_gui_memory,
    default_screen_belief,
    normalize_context_mode,
    should_inject_context,
)
from phone_agent.graph.state import AgentState
from phone_agent.graph.trace import JsonlTraceWriter
from phone_agent.graph.runtime_goal import RuntimeGoalContext
from phone_agent.graph.runtime_app_learning import RuntimeAppLearningContext
from phone_agent.grounding.factory import DEFAULT_GROUNDING_PROVIDER_NAME


@dataclass
class AgentConfig:
    """Configuration for the PhoneAgent."""

    max_steps: int = 100
    step_cap: int | None = None
    wall_clock_cap_seconds: float | None = None
    device_id: str | None = None
    lang: str = "cn"
    system_prompt: str | None = None
    verbose: bool = True
    trace_enabled: bool = True
    trace_dir: str = ".traces"
    trace_redact: bool = True
    trace_raw_model_response: bool = False
    trace_request_messages: bool = False
    trace_prompt_blocks: bool = False
    trace_unredacted_prompt: bool = False
    debug_full: bool = False
    trace_strict: bool = False
    context_mode: str = DEFAULT_CONTEXT_MODE
    prompt_version: str = PROMPT_VERSION
    grounding_provider_name: str | None = DEFAULT_GROUNDING_PROVIDER_NAME
    accessibility_marks: bool = False
    accessibility_timeout: float = 3.0
    accessibility_max_marks: int = 80
    goal_compile_retry: int = 1
    require_goal_approval: bool = False
    enable_prompt_cache: bool = False
    # P5 #1: skip the reflect model call when the deterministic verifier
    # answers the action question with high confidence and no goal-contract
    # vlm_judge evidence is still pending collection.
    skip_reflect_on_high_confidence: bool = True
    locateanything_context_max_chars: int = 0
    locateanything_structure_mode: str | None = None
    locateanything_max_visual_candidates: int = 30
    locateanything_visual_category_budget: int = 5
    locateanything_max_structure_calls: int = 5
    # HITL resume: with this flag the graph is compiled with a process-local
    # InMemorySaver and confirm/takeover interrupts can be resumed in-place via
    # run_live() instead of ending the run.
    enable_hitl_resume: bool = False
    # Optional fact extractors for typed-predicate evidence collection.
    # Both default to None; reflect only wires ExtractorFactProvider when a
    # callable is present (fact_providers.ExtractorFactProvider).
    visual_fact_extractor: Any | None = None
    whole_screen_fact_extractor: Any | None = None

    def __post_init__(self):
        if self.step_cap is None:
            self.step_cap = int(self.max_steps or 100)
        else:
            self.step_cap = int(self.step_cap or 100)
            self.max_steps = self.step_cap
        if self.wall_clock_cap_seconds is not None:
            self.wall_clock_cap_seconds = float(self.wall_clock_cap_seconds)
        self.context_mode = normalize_context_mode(self.context_mode)
        self.prompt_version = get_prompt_version(self.prompt_version)


@dataclass
class StepResult:
    """Result of a single agent step."""

    success: bool
    finished: bool
    action: dict[str, Any] | None
    thinking: str
    message: str | None = None


@dataclass
class RunResult:
    """Structured result for a full agent run."""

    success: bool = False
    finished: bool = False
    steps: int = 0
    duration: float = 0.0
    final_message: str = ""
    error: str | None = None
    hitl_count: int = 0
    trace_id: str = ""
    trace_path: str | None = None
    failure_cause: str | None = None
    retry_count: int = 0
    observation_retry_count: int = 0
    acceptance_round_count: int = 0
    locate_count: int = 0
    continuation_count: int = 0
    progress_claim_count: int = 0
    progress_claim_accepted: int = 0
    progress_claim_rejected: int = 0
    finish_source: str | None = None
    context_mode: str = DEFAULT_CONTEXT_MODE
    context_strategy: str = "unknown"
    prompt_version: str = PROMPT_VERSION
    selected_sections: list[str] = field(default_factory=list)
    context_block_chars: int = 0
    context_truncated: bool = False
    messages_before: int = 0
    messages_after: int = 0
    message_chars_before: int = 0
    message_chars_after: int = 0
    approx_tokens_before: int = 0
    approx_tokens_after: int = 0
    failure_memory_hit_count: int = 0
    repeated_failure_count: int = 0
    verifier_status: str | None = None
    verifier_failure_cause: str | None = None
    verifier_evidence: dict[str, Any] | None = None
    grounding_provider: str | None = None
    grounding_latency_ms: int | None = None
    grounding_failure_code: str | None = None
    grounding_screen_hash: str | None = None
    grounding_candidate_count: int = 0
    selected_grounding_candidate_id: int | None = None
    error_layer: str | None = None
    error_code: str | None = None
    recoverable: bool | None = None
    retry_policy: str | None = None
    finish_validation_status: str | None = None
    finish_validation_evidence: dict[str, Any] | None = None
    goal_contract_status: str | None = None
    goal_compile_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a JSON-friendly dictionary."""
        return asdict(self)


def _final_message_from_state(state: dict[str, Any]) -> str:
    if state.get("finished"):
        return "Task completed"
    cause = state.get("failure_cause")
    if cause == "resource_fuse_exhausted":
        return "Run stopped: resource fuse exhausted"
    if cause == "progress_evidence_exhausted":
        return "Run stopped: progress evidence exhausted"
    if state.get("error"):
        return str(state.get("error"))
    return "Run stopped before task completion"


def interrupt_payload(
    interrupt: Exception,
    default: str = "User intervention required",
) -> tuple[str, str]:
    """Extract (message, type) from a LangGraph HITL interrupt.

    Different langgraph versions store the interrupt values either on
    ``interrupt.interrupts`` or in ``exception.args``; the takeover node emits
    ``{"type": "takeover", "message": ...}``. Returns the first message found.
    """

    message = default
    interrupt_type = "takeover"
    items = getattr(interrupt, "interrupts", None)
    if not items and getattr(interrupt, "args", None):
        items = interrupt.args[0] if interrupt.args else None
    if isinstance(items, (list, tuple)):
        for item in items:
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                interrupt_type = str(value.get("type") or interrupt_type)
                message = str(value.get("message") or message)
                break
    elif isinstance(items, dict):
        interrupt_type = str(items.get("type") or interrupt_type)
        message = str(items.get("message") or message)
    return message, interrupt_type


def extract_interrupt(result: Any) -> tuple[str, str, str | None] | None:
    """Extract ``(message, type, prompt)`` from a ``__interrupt__`` marker.

    langgraph >= 1.x returns the pending interrupt in the invoke result under
    the ``__interrupt__`` key (a list of ``Interrupt`` objects) instead of
    raising ``GraphInterrupt`` once a checkpointer is attached; callers that
    want to resume must read the marker. Returns None when no interrupt is
    pending. ``prompt`` is the payload's own prompt string (e.g.
    confirm_node's "Confirm? (Y/N): " or goal_node's approval prompt) when the
    interrupt payload carries one, else None — callers fall back to their own
    assembled prompt.
    """

    if not isinstance(result, dict):
        return None
    marker = result.get("__interrupt__")
    if not marker:
        return None
    for item in marker:
        value = getattr(item, "value", None)
        if isinstance(value, dict):
            message = str(value.get("message") or "User intervention required")
            interrupt_type = str(value.get("type") or "takeover")
            prompt = value.get("prompt")
            return (
                message,
                interrupt_type,
                str(prompt) if prompt else None,
            )
    return None


class PhoneAgent:
    """
    AI-powered agent for automating Android phone interactions.

    Uses a LangGraph Plan-Execute-Reflect StateGraph to orchestrate
    the screenshot → VLM inference → action execution loop.

    Args:
        model_config: Configuration for the AI model.
        agent_config: Configuration for the agent behavior.

    Example:
        >>> from phone_agent import PhoneAgent
        >>> from phone_agent.model import ModelConfig
        >>>
        >>> model_config = ModelConfig(base_url="http://localhost:8000/v1")
        >>> agent = PhoneAgent(model_config)
        >>> agent.run("Open WeChat and send a message to John")
    """

    def __init__(
        self,
        model_config: ModelConfig | None = None,
        agent_config: AgentConfig | None = None,
    ):
        self.model_config = model_config or ModelConfig()
        self.agent_config = agent_config or AgentConfig()

        self.model_client = ModelClient(self.model_config)
        self._checkpointer = (
            build_hitl_checkpointer() if self.agent_config.enable_hitl_resume else None
        )
        self._graph = create_agent_graph(checkpointer=self._checkpointer)
        # P2: one LocateAnythingMLXProvider per agent, built once here and
        # injected through configurable["locate_provider"] so the
        # plan/observation/locate paths share the same (lazily loaded) MLX
        # model instance instead of rebuilding a ~2GB provider every step.
        self.locate_provider = self._build_singleton_locate_provider()

    def _build_singleton_locate_provider(self) -> Any:
        """Build the run-scoped locate/visual provider once (P2 RAM fix).

        Returns None for off/fake/accessibility-only configs (the explicit
        locate tool then derives its provider from the grounding config at
        call time, unchanged). For hybrid/locateanything configs this is the
        single lazily-loaded MLX instance shared across the whole run.
        """

        from phone_agent.grounding.factory import build_locate_provider

        return build_locate_provider(
            {
                "grounding_provider_name": self.agent_config.grounding_provider_name,
                "locateanything_context_max_chars": self.agent_config.locateanything_context_max_chars,
                "locateanything_structure_mode": self.agent_config.locateanything_structure_mode,
                "locateanything_max_visual_candidates": self.agent_config.locateanything_max_visual_candidates,
                "locateanything_visual_category_budget": self.agent_config.locateanything_visual_category_budget,
                "locateanything_max_structure_calls": self.agent_config.locateanything_max_structure_calls,
            }
        )

    def unload_models(self) -> None:
        """Release loaded model weights (LocateAnything MLX ~2GB) after a run.

        Safe to call any time: no-op when no provider (or a provider without
        an unload hook) is present; the next run lazily reloads.
        """

        provider = getattr(self, "locate_provider", None)
        unload = getattr(provider, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception:
                pass

    def run(self, task: str) -> str:
        """
        Run the agent to complete a task.

        Args:
            task: Natural language description of the task.

        Returns:
            Final message from the agent.
        """
        result = self.run_structured(task)
        if result.error:
            return f"Error: {result.error}"
        return result.final_message or _final_message_from_state(result.to_dict())

    def run_structured(self, task: str) -> RunResult:
        """Run the agent and return structured metrics for eval/trace consumers.

        Args:
            task: Natural language description of the task.

        Returns:
            RunResult containing completion status, step count, duration, error,
            HITL interrupt routing count, and trace id.
        """
        started_at = time.perf_counter()
        trace_id = str(uuid.uuid4())
        # P5 #3: run-scoped TTFT breaker counters must not leak across tasks
        # (eval harness runs many tasks sequentially on one process, and
        # interactive mode reuses the same agent for multiple runs).
        self.model_client.reset_run_state()
        device_factory = get_device_factory()
        trace_writer = self._build_trace_writer(trace_id)
        if trace_writer:
            trace_writer.emit("agent", "run_start", 0, {"task": task})

        try:
            screenshot = device_factory.get_screenshot(self.agent_config.device_id)
            initial_state = self._build_initial_state(task, screenshot)
            config = self._build_graph_config(device_factory, trace_id, trace_writer)
            result = self._graph.invoke(initial_state, config)
        except GraphInterrupt as interrupt:
            # F2.0: a HITL takeover interrupt is a clean terminal attribution, not
            # a run error. The interrupt carries the takeover message from
            # takeover_node; the run simply ends here (no resume path in eval/CLI
            # structured runs), so it must not be recorded as ``run_error``.
            message, interrupt_type = interrupt_payload(interrupt)
            if trace_writer:
                trace_writer.emit(
                    "agent",
                    "run_interrupted",
                    0,
                    {"type": interrupt_type, "message": message},
                )
            return RunResult(
                success=False,
                finished=True,
                steps=0,
                duration=time.perf_counter() - started_at,
                final_message=message,
                error=None,
                failure_cause="takeover",
                hitl_count=1,
                trace_id=trace_id,
                trace_path=str(trace_writer.path) if trace_writer else None,
                context_mode=self.agent_config.context_mode,
                prompt_version=self.agent_config.prompt_version,
            )
        except Exception as e:
            if trace_writer:
                trace_writer.emit("agent", "run_error", 0, {"message": str(e)})
            return RunResult(
                success=False,
                finished=True,
                steps=0,
                duration=time.perf_counter() - started_at,
                final_message=f"Error: {e}",
                error=str(e),
                hitl_count=0,
                trace_id=trace_id,
                trace_path=str(trace_writer.path) if trace_writer else None,
                context_mode=self.agent_config.context_mode,
                prompt_version=self.agent_config.prompt_version,
            )

        # F2.0a: langgraph >= 1.x returns a pending interrupt as a
        # ``__interrupt__`` marker in the invoke result even without a
        # checkpointer (verified on langgraph 1.2.2) — the GraphInterrupt
        # catch above only fires on older versions. Without this check the
        # real batch path fell through to _state_to_run_result and misreported
        # the HITL stop as a generic unfinished run (finished=False,
        # hitl_count=0, no run_interrupted trace). Attribute it exactly like
        # the exception path: clean terminal, never run_error.
        pending = extract_interrupt(result)
        if pending is not None:
            message, interrupt_type, _prompt = pending
            if trace_writer:
                trace_writer.emit(
                    "agent",
                    "run_interrupted",
                    0,
                    {"type": interrupt_type, "message": message},
                )
            return RunResult(
                success=False,
                finished=True,
                steps=int(result.get("step_count") or 0),
                duration=time.perf_counter() - started_at,
                final_message=message,
                error=None,
                failure_cause=interrupt_type,
                hitl_count=1,
                trace_id=trace_id,
                trace_path=str(trace_writer.path) if trace_writer else None,
                context_mode=self.agent_config.context_mode,
                prompt_version=self.agent_config.prompt_version,
            )

        run_result = self._state_to_run_result(
            result,
            time.perf_counter() - started_at,
            trace_id,
            str(trace_writer.path) if trace_writer else None,
        )
        if trace_writer:
            trace_writer.emit(
                "agent", "run_end", run_result.steps, run_result.to_dict()
            )
        return run_result

    def run_live(
        self,
        task: str,
        resume_input: Any = None,
    ) -> RunResult:
        """Run the agent interactively, resuming in place after HITL interrupts.

        Requires ``AgentConfig(enable_hitl_resume=True)``: the graph is
        compiled with a process-local checkpointer, so
        ``interrupt()`` in the confirm/takeover nodes pauses the run and
        returns a ``__interrupt__`` marker in the invoke result instead of
        ending it. For every interrupt the loop calls
        ``resume_input(prompt)`` (defaults to ``input()``) and resumes the
        graph with ``Command(resume=...)``.

        Per-interrupt-type semantics (F3):
        - ``takeover``: passes the answer through; Enter continues, ``n``/``no``
          aborts the run as a terminal ``RunResult`` with ``failure_cause`` =
          interrupt type (matching the structured-run attribution). An empty
          answer (plain Enter) resumes immediately.
        - ``confirmation``: the payload's own ``prompt`` (confirm_node's
          "Confirm? (Y/N): ") is used when present. Only an explicit
          ``y``/``yes`` resumes as ``"Y"``; every other input — empty Enter,
          ``n``, anything else — resumes as ``"N"`` (fail-closed), so
          confirm_node records ``finished=True`` and the graph terminates
          cleanly with the full trace.
        - ``goal_approval``: the payload's approval prompt is shown; ``y``/``yes``
          resumes as ``"Y"`` (approve), anything else as ``"N"`` (reject —
          goal_node falls back to the heuristic weak contract and continues).

        ``hitl_count`` accumulates across the loop. Trace is add-only:
        ``run_interrupted`` / ``run_resumed`` events are emitted per round.
        """

        if not self.agent_config.enable_hitl_resume:
            raise ValueError(
                "run_live requires AgentConfig(enable_hitl_resume=True)"
            )
        if resume_input is None:
            resume_input = input
        started_at = time.perf_counter()
        trace_id = str(uuid.uuid4())
        self.model_client.reset_run_state()
        device_factory = get_device_factory()
        trace_writer = self._build_trace_writer(trace_id)
        if trace_writer:
            trace_writer.emit("agent", "run_start", 0, {"task": task})
        hitl_count = 0

        def _terminal(
            message: str, interrupt_type: str, steps: int = 0
        ) -> RunResult:
            return RunResult(
                success=False,
                finished=True,
                steps=steps,
                duration=time.perf_counter() - started_at,
                final_message=message,
                error=None,
                failure_cause=interrupt_type,
                hitl_count=hitl_count,
                trace_id=trace_id,
                trace_path=str(trace_writer.path) if trace_writer else None,
                context_mode=self.agent_config.context_mode,
                prompt_version=self.agent_config.prompt_version,
            )

        try:
            screenshot = device_factory.get_screenshot(self.agent_config.device_id)
            initial_state = self._build_initial_state(task, screenshot)
            config = self._build_graph_config(device_factory, trace_id, trace_writer)
            result = self._graph.invoke(initial_state, config)
            while True:
                pending = extract_interrupt(result)
                if pending is None:
                    break
                message, interrupt_type, payload_prompt = pending
                hitl_count += 1
                if trace_writer:
                    trace_writer.emit(
                        "agent",
                        "run_interrupted",
                        0,
                        {"type": interrupt_type, "message": message},
                    )
                # F3: confirmation resumes are fail-closed per the node's own
                # semantics — only an explicit y/yes resumes as "Y"; an empty
                # Enter, "n", or any other input resumes as "N", so
                # confirm_node sets finished=True and the graph terminates
                # cleanly with full trace. We never return early here: the
                # graph itself ends the run.
                if interrupt_type == "confirmation":
                    prompt = payload_prompt or (
                        f"{message}\nConfirm? (Y/N): "
                        if initial_state.get("lang") == "en"
                        else f"{message}\n确认操作？(Y/N): "
                    )
                    answer = resume_input(prompt)
                    resume_value = (
                        "Y"
                        if str(answer or "").strip().lower() in ("y", "yes")
                        else "N"
                    )
                elif interrupt_type == "goal_approval":
                    # goal_node parses the resume itself: "N"/"NO"/"EDIT"
                    # rejects the contract (heuristic fallback, node continues),
                    # anything else approves. y/yes approves; every other input
                    # (empty Enter included) rejects fail-closed.
                    prompt = payload_prompt or (
                        f"{message}\nApprove the goal contract? (Y/N): "
                        if initial_state.get("lang") == "en"
                        else f"{message}\n批准目标契约？(Y/N): "
                    )
                    answer = resume_input(prompt)
                    resume_value = (
                        "Y"
                        if str(answer or "").strip().lower() in ("y", "yes")
                        else "N"
                    )
                else:
                    # takeover: unchanged semantics — Enter continues, n aborts.
                    prompt = payload_prompt or (
                        f"{message}\n完成后按回车继续（输入 n 终止）: "
                    )
                    answer = resume_input(prompt)
                    if answer is None or str(answer).strip().lower() in ("n", "no"):
                        return _terminal(
                            message,
                            interrupt_type,
                            int(result.get("step_count") or 0),
                        )
                    resume_value = answer
                if trace_writer:
                    trace_writer.emit(
                        "agent", "run_resumed", 0, {"type": interrupt_type}
                    )
                result = self._graph.invoke(Command(resume=resume_value), config)
        except GraphInterrupt as interrupt:
            # Defensive: only reachable when the graph was compiled without a
            # checkpointer (older langgraph raised instead of returning the
            # marker). Terminate with the same attribution as run_structured.
            message, interrupt_type = interrupt_payload(interrupt)
            hitl_count += 1
            if trace_writer:
                trace_writer.emit(
                    "agent",
                    "run_interrupted",
                    0,
                    {"type": interrupt_type, "message": message},
                )
            return _terminal(message, interrupt_type)
        except Exception as e:
            if trace_writer:
                trace_writer.emit("agent", "run_error", 0, {"message": str(e)})
            return RunResult(
                success=False,
                finished=True,
                steps=0,
                duration=time.perf_counter() - started_at,
                final_message=f"Error: {e}",
                error=str(e),
                hitl_count=hitl_count,
                trace_id=trace_id,
                trace_path=str(trace_writer.path) if trace_writer else None,
                context_mode=self.agent_config.context_mode,
                prompt_version=self.agent_config.prompt_version,
            )

        run_result = self._state_to_run_result(
            result,
            time.perf_counter() - started_at,
            trace_id,
            str(trace_writer.path) if trace_writer else None,
        )
        # F3: hitl_count consistency — the normal-completion path reads the
        # state counter, but the goal_approval interrupt never writes
        # state["hitl_count"] (only confirm/takeover nodes do), so the local
        # loop counter is the authoritative total when it is higher.
        run_result.hitl_count = max(run_result.hitl_count, hitl_count)
        if trace_writer:
            trace_writer.emit("agent", "run_end", run_result.steps, run_result.to_dict())
        return run_result

    def _build_initial_state(self, task: str, screenshot: Any) -> AgentState:
        """Build the initial LangGraph state for a task."""
        if self.agent_config.context_mode == "off":
            context_strategy = "off"
        elif should_inject_context(self.agent_config.context_mode):
            context_strategy = "inject_redacted_block"
        else:
            context_strategy = "observe_only"
        return {
            "task": task,
            "task_goal_contract": None,
            "goal_contract": None,
            "goal_contract_status": "pending",
            "goal_compile_source": None,
            "goal_compile_attempts": 0,
            "task_requirement_set": None,
            "contract_adequacy_status": None,
            "contract_adequacy_reasons": [],
            "needs_recompile": False,
            "messages": [],
            "step_count": 0,
            "max_steps": self.agent_config.step_cap,
            "step_cap": self.agent_config.step_cap,
            "wall_clock_cap_started_at": time.time(),
            "wall_clock_cap_seconds": self.agent_config.wall_clock_cap_seconds,
            "lang": self.agent_config.lang,
            "locate_count": 0,
            "invalidated_mark_ids": [],
            "continuation_count": 0,
            "continuation_last_latch_count": 0,
            "continuation_last_stage_index": None,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "screenshot_b64": None,
            "current_app": "",
            "screen_id": None,
            "screen_hash": None,
            "observation": None,
            "mark_registry": None,
            "thinking": "",
            "progress_note": None,
            "progress_claim": None,
            "progress_validation_status": None,
            "progress_claim_feedback": None,
            "progress_exhaustion_streak": 0,
            "progress_declaration_due": False,
            "progress_claim_round_count": 0,
            "progress_claim_grace_steps_remaining": 0,
            "action_raw": "",
            "action_parsed": None,
            "intent_raw": None,
            "grounding_error": None,
            "grounding_result": None,
            "grounding_provider": None,
            "grounding_latency_ms": None,
            "grounding_failure_code": None,
            "grounding_screen_hash": None,
            "grounding_observation": None,
            "grounding_candidates": [],
            "grounding_candidate_count": 0,
            "selected_grounding_candidate_id": None,
            "expected_outcome": None,
            "expected_transition": None,
            "parse_failure": None,
            "mechanism_suggestion": None,
            "validation_replan_count": 0,
            "error_layer": None,
            "error_code": None,
            "recoverable": None,
            "retry_policy": None,
            "action_result": None,
            "action_receipt": None,
            "pending_finish": False,
            "finish_claim": None,
            "finish_source": None,
            "finish_validation_status": None,
            "finish_validation_evidence": None,
            "goal_evidence_ledger": [],
            "task_plan_status": None,
            "stage_stall_windows": 0,
            "stage_stall_grace_windows": 0,
            "reflection": None,
            "action_succeeded": False,
            "reflection_verdict": None,
            "failure_cause": None,
            "suggested_strategy": None,
            "observation_retry_count": 0,
            "acceptance_round_count": 0,
            "acceptance_verdicts": {},
            "context_mode": self.agent_config.context_mode,
            "context_strategy": context_strategy,
            "prompt_version": self.agent_config.prompt_version,
            "selected_sections": [],
            "screen_belief": default_screen_belief(),
            "action_outcome_summary": None,
            "failure_memory": [],
            "summarized_history": "",
            "short_term_memory": {},
            "action_ledger": [],
            "context_budget": default_context_budget(),
            "context_truncated": False,
            "context_block_chars": 0,
            "messages_before": 0,
            "messages_after": 0,
            "message_chars_before": 0,
            "message_chars_after": 0,
            "approx_tokens_before": 0,
            "approx_tokens_after": 0,
            "failure_memory_hit_count": 0,
            "repeated_failure_count": 0,
            "repeated_action_detected": False,
            "repeat_rejected": False,
            "gui_memory": default_gui_memory(),
            "verifier_result": None,
            "verifier_status": None,
            "verifier_failure_cause": None,
            "verifier_evidence": None,
            "pending_interrupt": None,
            "interrupt_message": None,
            "interrupt_result": None,
            "pending_execute": False,
            "action_confirmed": False,
            "hitl_count": 0,
            "finished": False,
            "error": None,
            "device_id": self.agent_config.device_id,
        }

    def _build_trace_writer(self, trace_id: str) -> JsonlTraceWriter | None:
        """Build the best-effort local trace writer when enabled."""
        if not self.agent_config.trace_enabled:
            return None
        return JsonlTraceWriter(
            trace_id=trace_id,
            trace_dir=self.agent_config.trace_dir,
            redact=self.agent_config.trace_redact,
            allow_raw_debug=self.agent_config.trace_raw_model_response,
            allow_raw_request_debug=self.agent_config.trace_unredacted_prompt,
            strict=self.agent_config.trace_strict,
        )

    def _build_graph_config(
        self,
        device_factory: Any,
        trace_id: str,
        trace_writer: JsonlTraceWriter | None = None,
    ) -> dict[str, Any]:
        """Build LangGraph invocation config."""
        config = {
            "configurable": {
                "model_client": self.model_client,
                "device_factory": device_factory,
                "system_prompt": self.agent_config.system_prompt,
                "output_mode": self.model_config.output_mode,
                "verbose": self.agent_config.verbose,
                "trace_id": trace_id,
                "trace_writer": trace_writer,
                "trace_request_messages": self.agent_config.trace_request_messages,
                "trace_prompt_blocks": self.agent_config.trace_prompt_blocks,
                "trace_unredacted_prompt": self.agent_config.trace_unredacted_prompt,
                "debug_full": self.agent_config.debug_full,
                "context_mode": self.agent_config.context_mode,
                "prompt_version": self.agent_config.prompt_version,
                "grounding_provider_name": self.agent_config.grounding_provider_name,
                "accessibility_marks": self.agent_config.accessibility_marks,
                "accessibility_timeout": self.agent_config.accessibility_timeout,
                "accessibility_max_marks": self.agent_config.accessibility_max_marks,
                "goal_compile_retry": self.agent_config.goal_compile_retry,
                "require_goal_approval": self.agent_config.require_goal_approval,
                "enable_prompt_cache": self.agent_config.enable_prompt_cache,
                "skip_reflect_on_high_confidence": (
                    self.agent_config.skip_reflect_on_high_confidence
                ),
                "locateanything_context_max_chars": self.agent_config.locateanything_context_max_chars,
                "locateanything_structure_mode": self.agent_config.locateanything_structure_mode,
                "locateanything_max_visual_candidates": self.agent_config.locateanything_max_visual_candidates,
                "locateanything_visual_category_budget": self.agent_config.locateanything_visual_category_budget,
                "locateanything_max_structure_calls": self.agent_config.locateanything_max_structure_calls,
                "visual_fact_extractor": self.agent_config.visual_fact_extractor,
                "whole_screen_fact_extractor": self.agent_config.whole_screen_fact_extractor,
                "runtime_goal_context": RuntimeGoalContext(),
                "app_learning_context": RuntimeAppLearningContext(),
            }
        }
        # P2: the run-scoped LA singleton (built once in __init__) is injected
        # here. When absent (off/fake/accessibility-only), the key is omitted so
        # the locate tool's factory falls back to its config-derived provider.
        if self.locate_provider is not None:
            config["configurable"]["locate_provider"] = self.locate_provider
        # HITL resume: bind this run to a unique thread so the process-local
        # checkpointer keeps this run's checkpoint namespace (each run gets a
        # fresh trace_id, so resume cannot collide with a previous run).
        if self.agent_config.enable_hitl_resume:
            config["configurable"]["thread_id"] = trace_id
        return config

    def _state_to_run_result(
        self,
        state: dict[str, Any],
        duration: float,
        trace_id: str,
        trace_path: str | None = None,
    ) -> RunResult:
        """Convert final graph state into RunResult."""
        action_result = state.get("action_result") or {}
        error = state.get("error")
        final_message = action_result.get("message") or _final_message_from_state(state)
        success = (
            bool(state.get("finished"))
            and not error
            and bool(action_result.get("success", True))
        )

        context_metrics = build_context_metrics(state)
        return RunResult(
            success=success,
            finished=bool(state.get("finished")),
            steps=int(state.get("step_count") or 0),
            duration=duration,
            final_message=final_message,
            error=error,
            hitl_count=int(state.get("hitl_count") or 0),
            trace_id=trace_id,
            trace_path=trace_path,
            failure_cause=state.get("failure_cause"),
            retry_count=(
                int(state.get("observation_retry_count") or 0)
                + int(state.get("acceptance_round_count") or 0)
            ),
            observation_retry_count=int(state.get("observation_retry_count") or 0),
            acceptance_round_count=int(state.get("acceptance_round_count") or 0),
            locate_count=int(state.get("locate_count") or 0),
            continuation_count=int(state.get("continuation_count") or 0),
            progress_claim_count=int(state.get("progress_claim_round_count") or 0)
            + (1 if state.get("progress_validation_status") == "accepted" else 0),
            progress_claim_accepted=(
                1 if state.get("progress_validation_status") == "accepted" else 0
            ),
            progress_claim_rejected=int(state.get("progress_claim_round_count") or 0),
            finish_source=state.get("finish_source"),
            verifier_status=state.get("verifier_status"),
            verifier_failure_cause=state.get("verifier_failure_cause"),
            verifier_evidence=state.get("verifier_evidence"),
            grounding_provider=state.get("grounding_provider"),
            grounding_latency_ms=state.get("grounding_latency_ms"),
            grounding_failure_code=state.get("grounding_failure_code"),
            grounding_screen_hash=state.get("grounding_screen_hash"),
            grounding_candidate_count=int(state.get("grounding_candidate_count") or 0),
            selected_grounding_candidate_id=state.get(
                "selected_grounding_candidate_id"
            ),
            error_layer=state.get("error_layer"),
            error_code=state.get("error_code"),
            recoverable=state.get("recoverable"),
            retry_policy=state.get("retry_policy"),
            finish_validation_status=state.get("finish_validation_status"),
            finish_validation_evidence=state.get("finish_validation_evidence"),
            goal_contract_status=state.get("goal_contract_status"),
            goal_compile_source=state.get("goal_compile_source"),
            **context_metrics,
        )

    def reset(self) -> None:
        """Reset the agent state for a new task.

        The graph manages its own state per invocation, so this is a no-op
        kept for backward compatibility with main.py interactive mode.
        """
