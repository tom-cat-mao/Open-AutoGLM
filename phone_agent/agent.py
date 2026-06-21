"""Main PhoneAgent class for orchestrating phone automation."""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from phone_agent.device_factory import get_device_factory
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
from phone_agent.grounding.factory import DEFAULT_GROUNDING_PROVIDER_NAME


@dataclass
class AgentConfig:
    """Configuration for the PhoneAgent."""

    max_steps: int = 100
    device_id: str | None = None
    lang: str = "cn"
    system_prompt: str | None = None
    verbose: bool = True
    trace_enabled: bool = True
    trace_dir: str = ".traces"
    trace_redact: bool = True
    trace_strict: bool = False
    context_mode: str = DEFAULT_CONTEXT_MODE
    prompt_version: str = PROMPT_VERSION
    grounding_provider_name: str | None = DEFAULT_GROUNDING_PROVIDER_NAME
    accessibility_marks: bool = False
    accessibility_timeout: float = 3.0
    accessibility_max_marks: int = 80
    locateanything_context_max_chars: int = 0

    def __post_init__(self):
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a JSON-friendly dictionary."""
        return asdict(self)


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
        self._graph = create_agent_graph()

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
        return result.final_message or "Max steps reached"

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
        device_factory = get_device_factory()
        trace_writer = self._build_trace_writer(trace_id)
        if trace_writer:
            trace_writer.emit("agent", "run_start", 0, {"task": task})

        try:
            screenshot = device_factory.get_screenshot(self.agent_config.device_id)
            initial_state = self._build_initial_state(task, screenshot)
            config = self._build_graph_config(device_factory, trace_id, trace_writer)
            result = self._graph.invoke(initial_state, config)
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

        run_result = self._state_to_run_result(
            result,
            time.perf_counter() - started_at,
            trace_id,
            str(trace_writer.path) if trace_writer else None,
        )
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
            "messages": [],
            "step_count": 0,
            "max_steps": self.agent_config.max_steps,
            "lang": self.agent_config.lang,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "screenshot_b64": None,
            "current_app": "",
            "screen_id": None,
            "screen_hash": None,
            "observation": None,
            "mark_registry": None,
            "thinking": "",
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
            "error_layer": None,
            "error_code": None,
            "recoverable": None,
            "retry_policy": None,
            "action_result": None,
            "reflection": None,
            "action_succeeded": True,
            "reflection_verdict": None,
            "failure_cause": None,
            "suggested_strategy": None,
            "retry_count": 0,
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
            strict=self.agent_config.trace_strict,
        )

    def _build_graph_config(
        self,
        device_factory: Any,
        trace_id: str,
        trace_writer: JsonlTraceWriter | None = None,
    ) -> dict[str, Any]:
        """Build LangGraph invocation config."""
        return {
            "configurable": {
                "model_client": self.model_client,
                "device_factory": device_factory,
                "system_prompt": self.agent_config.system_prompt,
                "output_mode": self.model_config.output_mode,
                "verbose": self.agent_config.verbose,
                "trace_id": trace_id,
                "trace_writer": trace_writer,
                "context_mode": self.agent_config.context_mode,
                "prompt_version": self.agent_config.prompt_version,
                "grounding_provider_name": self.agent_config.grounding_provider_name,
                "accessibility_marks": self.agent_config.accessibility_marks,
                "accessibility_timeout": self.agent_config.accessibility_timeout,
                "accessibility_max_marks": self.agent_config.accessibility_max_marks,
                "locateanything_context_max_chars": self.agent_config.locateanything_context_max_chars,
            }
        }

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
        final_message = action_result.get("message") or (
            "Task completed" if state.get("finished") else "Max steps reached"
        )
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
            retry_count=int(state.get("retry_count") or 0),
            verifier_status=state.get("verifier_status"),
            verifier_failure_cause=state.get("verifier_failure_cause"),
            verifier_evidence=state.get("verifier_evidence"),
            grounding_provider=state.get("grounding_provider"),
            grounding_latency_ms=state.get("grounding_latency_ms"),
            grounding_failure_code=state.get("grounding_failure_code"),
            grounding_screen_hash=state.get("grounding_screen_hash"),
            grounding_candidate_count=int(state.get("grounding_candidate_count") or 0),
            selected_grounding_candidate_id=state.get("selected_grounding_candidate_id"),
            error_layer=state.get("error_layer"),
            error_code=state.get("error_code"),
            recoverable=state.get("recoverable"),
            retry_policy=state.get("retry_policy"),
            **context_metrics,
        )

    def reset(self) -> None:
        """Reset the agent state for a new task.

        The graph manages its own state per invocation, so this is a no-op
        kept for backward compatibility with main.py interactive mode.
        """
