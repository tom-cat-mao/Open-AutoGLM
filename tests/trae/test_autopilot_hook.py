from __future__ import annotations

import importlib.util
import json
import re
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".trae" / "hooks" / "autopilot.py"

spec = importlib.util.spec_from_file_location("autopilot_hook", HOOK_PATH)
assert spec and spec.loader
autopilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autopilot)


def write_graph(path: Path, approved: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
description: test
alwaysApply: false
globs: "*"
---

# RALPLAN Status

```yaml
design_status: {design_status}
last_critic_verdict: {verdict}
approved_for_execution: {approved_for_execution}
execution_status: not_started
```

# Roadmap
""".format(
            design_status="critic_approved" if approved else "draft",
            verdict="APPROVE" if approved else "ITERATE",
            approved_for_execution="true" if approved else "false",
        ),
        encoding="utf-8",
    )


def test_graph_is_approved(tmp_path: Path) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    assert autopilot.graph_is_approved(tmp_path)


def test_init_state_uses_current_approved_plan(tmp_path: Path) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    state = autopilot.init_state(
        {
            "cwd": str(tmp_path),
            "session_id": "s1",
            "prompt": "/autopilot --use-current-plan",
        }
    )
    assert state is not None
    active = autopilot.current_stage(state)
    assert active["id"] == "execution"
    assert state["pipeline_config"]["planning"] == "ralplan"


def test_init_state_defaults_to_ralplan_for_new_task(tmp_path: Path) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    state = autopilot.init_state(
        {
            "cwd": str(tmp_path),
            "session_id": "s1",
            "prompt": "/autopilot add feature",
        }
    )
    assert state is not None
    active = autopilot.current_stage(state)
    assert active["id"] == "ralplan"


def test_pipeline_config_direct_team_and_skips(tmp_path: Path) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    state = autopilot.init_state(
        {
            "cwd": str(tmp_path),
            "session_id": "s1",
            "prompt": "/autopilot --planning=direct --execution=team --verification=false --qa=false --max-stage-iterations=3 add feature",
        }
    )
    assert state is not None
    assert autopilot.current_stage(state)["id"] == "execution"
    assert state["pipeline_config"]["planning"] == "direct"
    assert state["pipeline_config"]["execution"] == "team"
    assert state["pipeline_config"]["verification"] is False
    assert state["pipeline_config"]["qa"] is False
    assert state["pipeline_config"]["max_stage_iterations"] == 3
    statuses = {stage["id"]: stage["status"] for stage in state["stages"]}
    assert statuses == {"ralplan": "skipped", "execution": "active", "ralph": "skipped", "qa": "skipped"}


def test_stage_prompt_includes_hud_and_team_subagents(tmp_path: Path) -> None:
    state = {
        "status": "active",
        "task": "demo",
        "pipeline_config": autopilot.build_pipeline_config({"execution": "team"}),
        "stages": autopilot.build_stages("execution", config=autopilot.build_pipeline_config({"execution": "team"})),
    }
    prompt = autopilot.stage_prompt("execution", state)
    assert "Pipeline" in prompt
    assert "Execution / Team" in prompt
    assert "designer" in prompt
    assert "executor" in prompt
    assert "debugger" in prompt
    assert "test-engineer" in prompt
    assert "主 Agent 负责合并" in prompt


def test_stage_prompts_reference_autopilot_agents() -> None:
    config = autopilot.build_pipeline_config({"execution": "team"})
    state = {
        "status": "active",
        "task": "demo",
        "pipeline_config": config,
        "stages": autopilot.build_stages("ralplan", config=config),
    }
    ralplan_prompt = autopilot.stage_prompt("ralplan", state)
    assert re.search(r"planner.*architect.*critic", ralplan_prompt, re.S)
    assert "Critic 未 APPROVE 前不得修改业务代码" in ralplan_prompt

    state["stages"] = autopilot.build_stages("ralph", config=config)
    ralph_prompt = autopilot.stage_prompt("ralph", state)
    for name in ["code-reviewer", "security-reviewer", "architect", "critic", "debugger", "test-engineer"]:
        assert name in ralph_prompt

    state["stages"] = autopilot.build_stages("qa", config=config)
    qa_prompt = autopilot.stage_prompt("qa", state)
    for name in ["test-engineer", "code-reviewer", "security-reviewer"]:
        assert name in qa_prompt
    assert "主 Agent 汇总" in qa_prompt


def parse_agent_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(?P<body>.*?)\n---", text, re.S)
    assert match, f"missing frontmatter: {path}"
    data: dict[str, str] = {}
    current_key: str | None = None
    for line in match.group("body").splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current_key:
            data[current_key] = (data[current_key] + "\n" + line.strip()).strip()
            continue
        assert ":" in line, f"invalid frontmatter line in {path}: {line}"
        key, value = line.split(":", 1)
        current_key = key.strip()
        data[current_key] = value.strip().strip('"\'|').strip()
    return data


def test_autopilot_agent_contracts_are_valid() -> None:
    agents_dir = ROOT / ".trae" / "agents"
    paths = sorted(agents_dir.glob("*.md"))
    metadata = {path.name: parse_agent_frontmatter(path) for path in paths}
    names = [item["name"] for item in metadata.values()]
    assert len(names) == len(set(names))

    expected = {
        "planner",
        "architect",
        "critic",
        "executor",
        "debugger",
        "test-engineer",
        "designer",
        "code-reviewer",
        "security-reviewer",
    }
    assert expected.issubset(set(names))
    for path, item in metadata.items():
        assert item.get("name"), path
        assert item.get("description"), path

    stage_agent_files = {
        "executor.md",
        "debugger.md",
        "test-engineer.md",
        "designer.md",
        "code-reviewer.md",
        "security-reviewer.md",
    }
    for filename in stage_agent_files:
        text = (agents_dir / filename).read_text(encoding="utf-8")
        assert "不主动 commit" in text, filename
        assert "不清理用户无关改动" in text, filename
        assert "已批准 roadmap" in text, filename


def test_autopilot_docs_and_hook_use_consistent_agents() -> None:
    config = autopilot.build_pipeline_config({"execution": "team"})
    prompts = {
        stage: autopilot.stage_prompt(stage, {"task": "demo", "pipeline_config": config, "stages": autopilot.build_stages(stage, config=config)})
        for stage in ["ralplan", "execution", "ralph", "qa"]
    }
    expected_by_stage = {
        "ralplan": set(autopilot.RALPLAN_AGENT_CHAIN),
        "execution": {"designer", "executor", "debugger", "test-engineer"},
        "ralph": {"code-reviewer", "security-reviewer", "architect", "critic", "debugger", "test-engineer"},
        "qa": {"test-engineer", "code-reviewer", "security-reviewer"},
    }
    for stage, names in expected_by_stage.items():
        for name in names:
            assert name in prompts[stage], f"{name} missing from {stage} prompt"

    agent_names = set(autopilot.RALPLAN_AGENT_CHAIN) | set(autopilot.AUTOPILOT_STAGE_AGENTS)
    docs = [
        ROOT / ".trae" / "rules" / "autopilot.mdc",
        ROOT / ".trae" / "skills" / "autopilot" / "SKILL.md",
        ROOT / ".trae" / "commands" / "autopilot.md",
    ]
    for name in agent_names:
        for path in docs:
            assert name in path.read_text(encoding="utf-8"), f"{name} missing from {path}"

    boundary_keywords = [".omc", ".trae/traecli.toml", "handoff", "subagent tracking", "不主动 commit"]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for keyword in boundary_keywords:
            assert keyword in text, f"{keyword} missing from {path}"


def test_traecli_escalation_protocol_is_documented() -> None:
    paths = [
        ROOT / ".trae" / "traecli.toml",
        ROOT / ".trae" / "skills" / "phone-agent-live-diagnosis" / "SKILL.md",
        ROOT / ".trae" / "rules" / "tools.mdc",
    ]
    required = [
        'sandbox_permissions="require_escalated"',
        "justification",
        "prefix_rule",
        "Operation not permitted",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for keyword in required:
            assert keyword in text, f"{keyword} missing from {path}"

    skill_text = paths[1].read_text(encoding="utf-8")
    assert "Do not merely tell the user" in skill_text
    assert "No Metal device available" in paths[2].read_text(encoding="utf-8")


def test_subagent_tracking_suppresses_stop(tmp_path: Path, capsys) -> None:
    state = {
        "status": "active",
        "session_id": "s1",
        "task": "demo",
        "pipeline_config": autopilot.build_pipeline_config({}),
        "stages": autopilot.build_stages("ralplan"),
    }
    autopilot.write_state(tmp_path, state)
    autopilot.track_subagent({"cwd": str(tmp_path), "session_id": "s1", "agent": "planner"}, True)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    autopilot.handle_stop({"cwd": str(tmp_path), "session_id": "s1", "transcript_path": str(transcript)})
    assert capsys.readouterr().out == ""


def test_transcript_signal_requires_assistant_marker(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    user_only = tmp_path / "user-only.jsonl"
    user_only.write_text(
        json.dumps({"role": "user", "content": "AUTOPILOT_SIGNAL: PIPELINE_RALPLAN_COMPLETE"})
        + "\n",
        encoding="utf-8",
    )
    assert not autopilot.transcript_has_signal(str(user_only), "PIPELINE_RALPLAN_COMPLETE")
    transcript.write_text(
        json.dumps({"role": "user", "content": "AUTOPILOT_SIGNAL: PIPELINE_RALPLAN_COMPLETE"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "AUTOPILOT_SIGNAL: PIPELINE_RALPLAN_COMPLETE"})
        + "\n",
        encoding="utf-8",
    )
    assert autopilot.transcript_has_signal(str(transcript), "PIPELINE_RALPLAN_COMPLETE")


def test_status_and_cancel_commands(tmp_path: Path, capsys) -> None:
    autopilot.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/autopilot status"})
    assert "Autopilot is idle" in capsys.readouterr().out

    autopilot.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/autopilot cancel"})
    assert "Autopilot cancelled" in capsys.readouterr().out
    state = autopilot.read_state(tmp_path)
    assert state["status"] == "cancelled"


def test_advance_state_moves_to_next_stage(tmp_path: Path) -> None:
    state = {
        "status": "active",
        "stages": autopilot.build_stages("ralplan", no_verification=False, no_qa=False),
    }
    state, previous, next_stage = autopilot.advance_state(state)
    assert previous == "ralplan"
    assert next_stage == "execution"
    assert autopilot.current_stage(state)["id"] == "execution"


def test_stop_hook_waits_then_advances_on_signal(tmp_path: Path, capsys) -> None:
    state = {
        "status": "active",
        "session_id": "s1",
        "task": "demo",
        "stages": autopilot.build_stages("ralplan", no_verification=False, no_qa=False),
    }
    autopilot.write_state(tmp_path, state)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    autopilot.handle_stop({"cwd": str(tmp_path), "session_id": "s1", "transcript_path": str(transcript)})
    assert capsys.readouterr().out == ""

    transcript.write_text(
        json.dumps({"role": "assistant", "content": "AUTOPILOT_SIGNAL: PIPELINE_RALPLAN_COMPLETE"})
        + "\n",
        encoding="utf-8",
    )
    autopilot.handle_stop({"cwd": str(tmp_path), "session_id": "s1", "transcript_path": str(transcript)})
    advanced = json.loads(capsys.readouterr().out)
    assert advanced["decision"] == "block"
    assert "Stage complete: ralplan -> execution" in advanced["reason"]
    saved = autopilot.read_state(tmp_path)
    assert autopilot.current_stage(saved)["id"] == "execution"


def test_mode_registry_blocks_conflicting_mode(tmp_path: Path, capsys) -> None:
    registry = tmp_path / ".trae" / "modes" / "state.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({"active": {"mode": "ralplan", "session_id": "other"}}),
        encoding="utf-8",
    )
    autopilot.handle_user_prompt_submit({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/autopilot add tests"})
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "block"
    assert "ralplan mode is active" in output["reason"]


def test_use_current_plan_consumes_ralplan_pending_handoff(tmp_path: Path) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    registry = tmp_path / ".trae" / "modes" / "state.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"active": {"mode": "ralplan", "session_id": "s1"}}), encoding="utf-8")
    ralplan_state = tmp_path / ".trae" / "ralplan" / "state.json"
    ralplan_state.parent.mkdir(parents=True, exist_ok=True)
    ralplan_state.write_text(
        json.dumps({"active": True, "current_phase": "pending_approval", "phase": "pending_approval", "status": "pending_approval"}),
        encoding="utf-8",
    )

    state = autopilot.init_state({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/autopilot --use-current-plan"})

    assert state is not None
    assert autopilot.current_stage(state)["id"] == "execution"
    saved_ralplan = json.loads(ralplan_state.read_text(encoding="utf-8"))
    assert saved_ralplan["active"] is False
    assert saved_ralplan["current_phase"] == "handoff"
    saved_registry = json.loads(registry.read_text(encoding="utf-8"))
    assert saved_registry["active"]["mode"] == "autopilot"


def test_resume_expires_old_state(tmp_path: Path, capsys) -> None:
    state = {
        "status": "cancelled",
        "session_id": "old",
        "task": "demo",
        "updated_at": (autopilot.now() - timedelta(hours=25)).isoformat(),
        "stages": autopilot.build_stages("execution", no_verification=True, no_qa=True),
    }
    autopilot.write_state(tmp_path, state)
    autopilot.handle_user_prompt_submit({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/autopilot resume"})
    json.loads(capsys.readouterr().out)
    saved = autopilot.read_state(tmp_path)
    assert saved["status"] == "expired"


def test_natural_language_autopilot_requires_task_size() -> None:
    assert not autopilot.should_start("use autopilot")
    assert autopilot.should_start("use autopilot to implement tests/trae/test_autopilot_hook.py coverage")


def test_transition_log_written_on_init_and_advance(tmp_path: Path) -> None:
    state = autopilot.init_state({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/autopilot --direct demo"})
    assert state is not None
    log_path = tmp_path / ".trae" / "autopilot" / "transitions.jsonl"
    assert "on_enter" in log_path.read_text(encoding="utf-8")

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": "AUTOPILOT_SIGNAL: PIPELINE_EXECUTION_COMPLETE"}) + "\n",
        encoding="utf-8",
    )
    autopilot.handle_stop({"cwd": str(tmp_path), "session_id": "s1", "transcript_path": str(transcript)})
    assert "on_exit" in log_path.read_text(encoding="utf-8")
