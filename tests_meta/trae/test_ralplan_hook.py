from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ralplan_boundary_uses_omx_consensus_planning() -> None:
    rule = (ROOT / ".trae" / "rules" / "ralplan.mdc").read_text(encoding="utf-8")
    skill = (ROOT / ".codex" / "skills" / "ralplan" / "SKILL.md").read_text(encoding="utf-8")

    assert "不再维护 Trae-native RALPLAN 状态机" in rule
    assert "$ralplan == $plan --consensus" in rule
    assert "Planner -> Architect -> Critic" in rule
    assert "Planner" in skill and "Architect" in skill and "Critic" in skill


def test_legacy_ralplan_surfaces_are_not_restored() -> None:
    removed = [
        ROOT / ".trae" / "skills" / "ralplan",
        ROOT / ".trae" / "commands" / "ralplan.md",
        ROOT / ".trae" / "hooks" / "ralplan.py",
    ]
    for path in removed:
        assert not path.exists(), f"legacy RALPLAN surface restored: {path}"

    rule = (ROOT / ".trae" / "rules" / "ralplan.mdc").read_text(encoding="utf-8")
    for text in (".trae/skills/ralplan/", ".trae/commands/ralplan.md", ".trae/hooks/ralplan.py"):
        assert text in rule


def test_ralplan_artifact_and_execution_boundaries_are_documented() -> None:
    rule = (ROOT / ".trae" / "rules" / "ralplan.mdc").read_text(encoding="utf-8")
    assert ".omx/context/**" in rule
    assert ".omx/plans/**" in rule
    assert ".omx/specs/**" in rule
    assert ".omx/state/**" in rule
    assert "Critic approval 前不得进入实现" in rule
    assert "$ultragoal" in rule
