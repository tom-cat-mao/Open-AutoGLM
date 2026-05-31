from __future__ import annotations

import importlib.util
import json
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
    assert "Explore" in prompt
    assert "general-purpose" in prompt


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


def test_stop_hook_blocks_then_advances_on_signal(tmp_path: Path, capsys) -> None:
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
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["decision"] == "block"
    assert "PIPELINE_RALPLAN_COMPLETE" in blocked["reason"]

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
