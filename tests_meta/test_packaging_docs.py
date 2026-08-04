import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setup_install_requires_contains_runtime_graph_deps() -> None:
    setup_ast = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    setup_call = next(
        node
        for node in ast.walk(setup_ast)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setup"
    )
    install_requires = next(
        keyword.value
        for keyword in setup_call.keywords
        if keyword.arg == "install_requires"
    )
    deps = {item.value.split(">=", 1)[0] for item in install_requires.elts}

    assert {"Pillow", "openai", "langgraph", "langchain-core", "requests"} <= deps


def test_gitignore_does_not_ignore_tests_directory() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "tests/" not in lines


def test_readme_topology_matches_confirm_then_execute_route() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "confirm → after_interrupt → [execute|reflect|end]" in readme
    assert "takeover → after_interrupt → [reflect|end]" in readme


def test_future_roadmap_has_no_unverified_test_count_or_pyproject_assumption() -> None:
    roadmap = (ROOT / "docs" / "future-roadmap.md").read_text(encoding="utf-8")

    assert "89/89 passing" not in roadmap
    assert "pyproject.toml" not in roadmap
