"""Thin-loop v2 core: config, model factory, device session, coords, prompts.

This package implements the v2 "thin loop + tooling" architecture described in
``AGENTS.md``. The v1 LangGraph node graph (goal -> plan ->
execute -> reflect -> acceptance) has been removed; v2 drives the device through
a single LLM call per step via tools, with the harness responsible only for tool
supply, safety boundaries, context hygiene, and observability.

W-core owns the foundation layer here: :mod:`phone_agent.v2.config` (three-tier
env/.env/CLI resolution), :mod:`phone_agent.v2.model` (ChatOpenAI factory),
:mod:`phone_agent.v2.session` (device-side run state + marks + locate),
:mod:`phone_agent.v2.coords` (0-1000 -> pixel conversion), and
:mod:`phone_agent.v2.prompts` (minimal system prompt).
"""
