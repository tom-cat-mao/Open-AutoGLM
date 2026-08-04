"""P2: LA model singleton — one provider per agent, injected into the
plan/observation/locate paths; unload releases the model after a run."""

from phone_agent.agent import AgentConfig, PhoneAgent
from phone_agent.grounding.factory import build_locate_provider, build_mark_providers
from phone_agent.grounding.fallback import FallbackMarkProvider
from phone_agent.grounding.locateanything import LocateAnythingMLXProvider
from phone_agent.model import ModelConfig


def _agent(grounding_provider_name: str = "hybrid") -> PhoneAgent:
    return PhoneAgent(
        ModelConfig(),
        AgentConfig(grounding_provider_name=grounding_provider_name),
    )


def test_agent_builds_single_locate_provider_for_hybrid() -> None:
    agent = _agent()
    assert isinstance(agent.locate_provider, LocateAnythingMLXProvider)


def test_agent_does_not_build_provider_for_off_config() -> None:
    agent = _agent("off")
    assert agent.locate_provider is None


def test_graph_config_injects_the_same_singleton_instance() -> None:
    agent = _agent()
    first = agent._build_graph_config(None, "t1")
    second = agent._build_graph_config(None, "t2")

    assert first["configurable"]["locate_provider"] is agent.locate_provider
    assert second["configurable"]["locate_provider"] is agent.locate_provider


def test_locate_tool_derives_provider_from_injected_singleton() -> None:
    """The locate tool's factory must return the injected instance (identity),
    so every locate query reuses the same lazily-loaded model."""
    agent = _agent()
    configurable = agent._build_graph_config(None, "t1")["configurable"]

    provider = build_locate_provider(configurable)
    assert provider is agent.locate_provider


def test_mark_providers_hybrid_child_is_the_injected_singleton() -> None:
    """plan/observation_capture build_mark_providers reuse the injected LA
    instance; only the accessibility child is rebuilt per step."""
    agent = _agent()
    configurable = agent._build_graph_config(None, "t1")["configurable"]
    configurable["accessibility_tree_dump"] = None

    providers = build_mark_providers(configurable)
    assert len(providers) == 1
    fallback = providers[0]
    assert isinstance(fallback, FallbackMarkProvider)
    assert any(child is agent.locate_provider for child in fallback.providers)


def test_unload_models_delegates_to_provider_unload(monkeypatch) -> None:
    agent = _agent()
    calls = {"unloaded": 0}

    def fake_unload():
        calls["unloaded"] += 1

    monkeypatch.setattr(agent.locate_provider, "unload", fake_unload)

    agent.unload_models()
    assert calls["unloaded"] == 1


def test_unload_models_is_noop_without_provider() -> None:
    agent = _agent("off")
    agent.unload_models()  # must not raise
