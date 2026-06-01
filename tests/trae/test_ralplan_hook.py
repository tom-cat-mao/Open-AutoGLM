from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".trae" / "hooks" / "ralplan.py"

spec = importlib.util.spec_from_file_location("ralplan_hook", HOOK_PATH)
assert spec and spec.loader
ralplan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ralplan)


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
status: {status}
design_status: {design_status}
last_critic_verdict: {verdict}
approved_for_execution: {approved_for_execution}
execution_status: not_started
```

# Roadmap
""".format(
            status="critic_approved" if approved else "draft",
            design_status="critic_approved" if approved else "draft",
            verdict="APPROVE" if approved else "ITERATE",
            approved_for_execution="true" if approved else "false",
        ),
        encoding="utf-8",
    )


def test_explicit_ralplan_invocation_initializes_state(tmp_path: Path) -> None:
    state = ralplan.init_state(
        {"cwd": str(tmp_path), "session_id": "s1"},
        "migrate auth flow",
        {"--deliberate"},
        source="explicit",
    )
    assert state["active"] is True
    assert state["current_phase"] == "ralplan"
    assert state["deliberate"] is True
    assert ralplan.read_state(tmp_path)["task"] == "migrate auth flow"


def test_pre_execution_gate_requires_missing_anchor() -> None:
    assert ralplan.is_underspecified_for_execution("ralph fix this")
    assert not ralplan.is_underspecified_for_execution("ralph fix phone_agent/agent.py")
    assert not ralplan.is_underspecified_for_execution("/autopilot fix this")


def test_chinese_explicit_invocation() -> None:
    assert ralplan.has_explicit_ralplan_invocation("请进入共识规划")
    assert ralplan.has_explicit_ralplan_invocation("先规划再执行这个需求")
    assert ralplan.has_explicit_ralplan_invocation("使用规划模式")


def test_handle_user_prompt_submit_gate_blocks_with_ralplan(tmp_path: Path, capsys) -> None:
    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "session_id": "s1", "prompt": "ralph fix this"})
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "RALPLAN GATE" in out["reason"]
    assert ralplan.read_state(tmp_path)["source"] == "pre_execution_gate"


def test_ralplan_start_blocks_conflicting_mode(tmp_path: Path, capsys) -> None:
    registry = tmp_path / ".trae" / "modes" / "state.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"active": {"mode": "autopilot", "session_id": "s1"}}), encoding="utf-8")
    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/ralplan plan this"})
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "autopilot mode is active" in out["reason"]


def test_slash_ralplan_start_allows_prompt_command_to_run(tmp_path: Path, capsys) -> None:
    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "session_id": "s1", "prompt": "/ralplan plan this"})
    assert capsys.readouterr().out == ""
    state = ralplan.read_state(tmp_path)
    assert state["source"] == "prompt_command"
    assert state["task"] == "plan this"


def test_slash_ralplan_stop_ignores_stale_approved_graph(tmp_path: Path, capsys) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    ralplan.write_state(
        tmp_path,
        {
            "active": True,
            "session_id": "s1",
            "current_phase": "ralplan",
            "phase": "ralplan",
            "status": "active",
            "task": "demo",
            "source": "prompt_command",
        },
    )
    ralplan.handle_stop({"cwd": str(tmp_path), "session_id": "s1"})
    assert capsys.readouterr().out == ""
    assert ralplan.read_state(tmp_path)["current_phase"] == "ralplan"


def test_status_cancel_reset_commands(tmp_path: Path, capsys) -> None:
    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/ralplan status"})
    assert "RALPLAN is idle" in json.loads(capsys.readouterr().out)["reason"]

    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/ralplan cancel"})
    assert "cancel" in json.loads(capsys.readouterr().out)["reason"].lower()
    assert ralplan.read_state(tmp_path)["current_phase"] == "cancelled"

    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/ralplan reset"})
    assert "reset" in json.loads(capsys.readouterr().out)["reason"]
    assert ralplan.read_state(tmp_path) is None


def test_stop_does_not_inject_continuation_when_not_approved(tmp_path: Path, capsys) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=False)
    ralplan.write_state(
        tmp_path,
        {
            "active": True,
            "session_id": "s1",
            "current_phase": "ralplan",
            "phase": "ralplan",
            "status": "active",
            "task": "demo",
        },
    )
    ralplan.handle_stop({"cwd": str(tmp_path), "session_id": "s1"})
    assert capsys.readouterr().out == ""


def test_stop_moves_to_pending_approval_when_graph_approved(tmp_path: Path, capsys) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    ralplan.write_state(
        tmp_path,
        {
            "active": True,
            "session_id": "s1",
            "current_phase": "ralplan",
            "phase": "ralplan",
            "status": "active",
            "task": "demo",
        },
    )
    ralplan.handle_stop({"cwd": str(tmp_path), "session_id": "s1"})
    out = json.loads(capsys.readouterr().out)
    assert "PENDING APPROVAL" in out["systemMessage"]
    state = ralplan.read_state(tmp_path)
    assert state["current_phase"] == "pending_approval"
    assert state["awaiting_confirmation"] is True


def test_approve_requires_critic_approved_graph(tmp_path: Path, capsys) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=True)
    ralplan.write_state(tmp_path, {"active": True, "current_phase": "pending_approval", "status": "pending_approval"})
    ralplan.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "/ralplan approve"})
    assert "approved" in json.loads(capsys.readouterr().out)["reason"]
    state = ralplan.read_state(tmp_path)
    assert state["active"] is False
    assert state["current_phase"] == "handoff"


def test_subagent_tracking_suppresses_stop_reinforcement(tmp_path: Path, capsys) -> None:
    write_graph(tmp_path / ".trae" / "rules" / "graph.mdc", approved=False)
    ralplan.write_state(tmp_path, {"active": True, "session_id": "s1", "current_phase": "ralplan", "status": "active"})
    ralplan.track_subagent({"cwd": str(tmp_path), "agent_id": "a1", "agent_type": "planner"}, active=True)
    ralplan.handle_stop({"cwd": str(tmp_path), "session_id": "s1"})
    assert capsys.readouterr().out == ""
