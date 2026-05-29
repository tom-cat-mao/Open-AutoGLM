"""Main PhoneAgent class for orchestrating phone automation."""

from dataclasses import dataclass
from typing import Any

from phone_agent.device_factory import get_device_factory
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.graph.builder import create_agent_graph
from phone_agent.graph.state import AgentState


@dataclass
class AgentConfig:
    """Configuration for the PhoneAgent."""

    max_steps: int = 100
    device_id: str | None = None
    lang: str = "cn"
    system_prompt: str | None = None
    verbose: bool = True

    def __post_init__(self):
        if self.system_prompt is None:
            from phone_agent.config import get_system_prompt
            self.system_prompt = get_system_prompt(self.lang)


@dataclass
class StepResult:
    """Result of a single agent step."""

    success: bool
    finished: bool
    action: dict[str, Any] | None
    thinking: str
    message: str | None = None


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
        device_factory = get_device_factory()
        screenshot = device_factory.get_screenshot(self.agent_config.device_id)

        initial_state: AgentState = {
            "task": task,
            "messages": [],
            "step_count": 0,
            "max_steps": self.agent_config.max_steps,
            "lang": self.agent_config.lang,
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "screenshot_b64": None,
            "current_app": "",
            "thinking": "",
            "action_raw": "",
            "action_parsed": None,
            "action_result": None,
            "reflection": None,
            "action_succeeded": True,
            "pending_interrupt": None,
            "interrupt_message": None,
            "interrupt_result": None,
            "pending_execute": False,
            "action_confirmed": False,
            "finished": False,
            "error": None,
            "device_id": self.agent_config.device_id,
        }

        config = {
            "configurable": {
                "model_client": self.model_client,
                "device_factory": device_factory,
                "system_prompt": self.agent_config.system_prompt,
                "verbose": self.agent_config.verbose,
            }
        }

        result = self._graph.invoke(initial_state, config)

        if result.get("error"):
            return f"Error: {result['error']}"
        if result.get("action_result"):
            return result["action_result"].get("message") or "Task completed"
        return "Max steps reached"

    def reset(self) -> None:
        """Reset the agent state for a new task.

        The graph manages its own state per invocation, so this is a no-op
        kept for backward compatibility with main.py interactive mode.
        """
