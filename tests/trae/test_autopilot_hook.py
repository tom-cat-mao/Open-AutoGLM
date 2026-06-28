from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_autopilot_boundary_uses_omx_canonical_workflow() -> None:
    rule = (ROOT / ".trae" / "rules" / "autopilot.mdc").read_text(encoding="utf-8")
    skill = (ROOT / ".codex" / "skills" / "autopilot" / "SKILL.md").read_text(encoding="utf-8")

    workflow = "$deep-interview -> $ralplan -> $ultragoal (+ $team if needed) -> $code-review -> $ultraqa"
    assert "不再维护 Trae-native Autopilot 状态机" in rule
    assert workflow in rule
    assert workflow in skill
    assert ".codex/skills/autopilot/SKILL.md" in rule


def test_legacy_autopilot_surfaces_are_not_restored() -> None:
    removed = [
        ROOT / ".trae" / "skills" / "autopilot",
        ROOT / ".trae" / "commands" / "autopilot.md",
        ROOT / ".trae" / "hooks" / "autopilot.py",
    ]
    for path in removed:
        assert not path.exists(), f"legacy Autopilot surface restored: {path}"

    rule = (ROOT / ".trae" / "rules" / "autopilot.mdc").read_text(encoding="utf-8")
    for text in (".trae/skills/autopilot/", ".trae/commands/autopilot.md", ".trae/hooks/autopilot.py"):
        assert text in rule


def test_autopilot_runtime_state_boundary_is_documented() -> None:
    rule = (ROOT / ".trae" / "rules" / "autopilot.mdc").read_text(encoding="utf-8")
    assert ".omx/state/**" in rule
    assert ".omx/context/**" in rule
    assert ".omx/plans/**" in rule
    assert ".omx/ultragoal/**" in rule
    assert "AGENTS.md" in rule
    assert ".trae/rules/graph.mdc" in rule
