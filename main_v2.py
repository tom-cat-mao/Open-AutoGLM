#!/usr/bin/env python
"""v2 CLI entry point for the thin-loop PhoneAgent.

Usage:
    .venv/bin/python main_v2.py "task description" --device-id X --max-steps 20 \
        --model M --base-url U --grounding-provider hybrid --lang cn --trace-dir .traces

Resolution order: CLI overrides > shell env > project .env > defaults. All flags
default to ``None`` so unset flags never clobber env-derived values.

Exit codes: success 0 / error 1 / takeover 2 / budget-or-fuse exhausted 3.

See ``AGENTS.md`` §11 for the binding contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from phone_agent.v2.capabilities import (
    CapabilityAssemblyContext,
    assemble_capabilities,
    build_capability_registry,
)
from phone_agent.v2.config import V2Config, load_project_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main_v2.py",
        description="TaskWizard thin-loop v2 PhoneAgent CLI",
    )
    parser.add_argument("task", nargs="?", default=None, help="natural-language task description")
    parser.add_argument("--device-id", default=None, help="ADB device serial")
    parser.add_argument("--max-steps", type=int, default=None, help="runaway-loop fuse: max model calls (PHONE_AGENT_MAX_STEPS, default 100)")
    parser.add_argument("--model", default=None, help="model id (PHONE_AGENT_MODEL)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--grounding-provider", default=None, help="grounding provider name")
    parser.add_argument("--lang", default=None, help="prompt language (cn/en)")
    parser.add_argument("--trace-dir", default=None, help="trace output directory")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument(
        "--dream",
        action="store_true",
        help="consolidate the local App-KB instead of running a phone task",
    )
    maintenance.add_argument(
        "--rebuild-vec",
        action="store_true",
        help="rebuild the semantic recall index from episode/App-KB JSONL",
    )
    maintenance.add_argument(
        "--distill",
        action="store_true",
        help="distill episode outcomes into proposed lessons (offline)",
    )
    maintenance.add_argument(
        "--review-lessons",
        action="store_true",
        help="interactively review proposed lessons",
    )
    maintenance.add_argument(
        "--approve-lesson",
        metavar="ID",
        help="approve one Rule-of-3-qualified lesson",
    )
    maintenance.add_argument(
        "--revoke-lesson",
        nargs=2,
        metavar=("ID", "REASON"),
        help="revoke one lesson with a reason",
    )
    maintenance.add_argument(
        "--supersede-lesson",
        nargs=2,
        metavar=("ID", "TEXT"),
        help="create a proposed next version of a lesson",
    )
    return parser


def _overrides_from_args(args: argparse.Namespace) -> dict:
    """Map CLI flags to V2Config field names; None values are dropped by from_env."""

    return {
        "device_id": args.device_id,
        "max_model_calls": args.max_steps,
        "model_name": args.model,
        "base_url": args.base_url,
        "grounding_provider": args.grounding_provider,
        "lang": args.lang,
        "trace_dir": args.trace_dir,
    }


def _device_inventory(config: V2Config) -> set[str] | None:
    """Return the current installed-package set, or None when unavailable."""

    try:
        from phone_agent.device_factory import get_device_factory

        inventory = get_device_factory().get_installed_app_inventory(config.device_id)
        packages = set(getattr(inventory, "packages", ()) or ())
        return packages or None
    except Exception:  # noqa: BLE001 - dream is fail-open when the device is absent
        return None


def _run_dream(
    config: V2Config,
    *,
    light: bool,
    store: Any | None = None,
) -> dict[str, Any]:
    """Maintain App-KB and experience through the shared dream implementation."""

    from phone_agent.v2.dream import run_maintenance

    return run_maintenance(
        config,
        light=light,
        store=store,
        inventory_provider=lambda: _device_inventory(config),
    )


def _print_dream_summary(summary: dict[str, Any]) -> None:
    print(f"dream: {json.dumps(summary, ensure_ascii=False, sort_keys=True)}")


def _lesson_store(config: V2Config) -> Any:
    from phone_agent.v2.evolution import LessonStore

    return LessonStore(config.lessons_dir)


def _approve_lesson(config: V2Config, lesson_id: str) -> dict[str, Any]:
    from phone_agent.v2.evolution import approve_if_eligible, read_episode_outcomes

    candidate = approve_if_eligible(
        _lesson_store(config),
        lesson_id,
        read_episode_outcomes(config.experience_dir),
    )
    return candidate.to_dict()


def _review_lessons(config: V2Config) -> dict[str, int]:
    """Interactively approve/revoke proposals; skip leaves the log untouched."""

    from phone_agent.v2.evolution import evaluate_promotion, read_episode_outcomes

    store = _lesson_store(config)
    episodes = read_episode_outcomes(config.experience_dir)
    reviewed = approved = revoked = 0
    for candidate in store.lessons(status="proposed"):
        evaluation = evaluate_promotion(
            candidate,
            episodes,
            approved_lessons=store.lessons(status="approved"),
        )
        print(json.dumps(evaluation.candidate.to_dict(), ensure_ascii=False, indent=2))
        verdict = input("[a]pprove / [r]evoke / [s]kip: ").strip().lower()
        reviewed += 1
        if verdict in {"a", "approve"}:
            if not evaluation.eligible:
                print("blocked: " + ", ".join(evaluation.reasons), file=sys.stderr)
                continue
            store.approve(candidate.lesson_id)
            approved += 1
        elif verdict in {"r", "revoke"}:
            reason = input("reason: ").strip()
            try:
                store.revoke(candidate.lesson_id, reason)
            except ValueError as exc:
                print(f"blocked: {exc}", file=sys.stderr)
            else:
                revoked += 1
    return {"reviewed": reviewed, "approved": approved, "revoked": revoked}


def _maintenance_requested(args: argparse.Namespace) -> bool:
    return bool(
        args.dream
        or args.rebuild_vec
        or args.distill
        or args.review_lessons
        or args.approve_lesson
        or args.revoke_lesson
        or args.supersede_lesson
    )


def _maintenance_command(args: argparse.Namespace) -> str | None:
    for name in (
        "dream",
        "rebuild_vec",
        "distill",
        "review_lessons",
        "approve_lesson",
        "revoke_lesson",
        "supersede_lesson",
    ):
        if getattr(args, name, None):
            return name
    return None


def _build_cli_capability_context(config: V2Config) -> CapabilityAssemblyContext:
    """Mount maintenance commands through the same capability registry."""

    def dream(_args: argparse.Namespace) -> int:
        _print_dream_summary(_run_dream(config, light=False))
        return 0

    def rebuild_vec(_args: argparse.Namespace) -> int:
        from phone_agent.v2.recall import rebuild_index

        print(
            "vec: "
            + json.dumps(rebuild_index(config), ensure_ascii=False, sort_keys=True)
        )
        return 0

    def distill(_args: argparse.Namespace) -> int:
        if config.evolution_mode == "off":
            print("error: PHONE_AGENT_EVOLUTION=off disables --distill", file=sys.stderr)
            return 1
        from phone_agent.v2.evolution import build_distill_model, distill_lessons

        result = distill_lessons(
            f"{config.experience_dir}/events.jsonl",
            config.lessons_dir,
            model=build_distill_model(config),
            token_budget=config.token_budget,
        )
        print(
            "distill: "
            + json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        return 0

    def review_lessons(_args: argparse.Namespace) -> int:
        print(
            "review: "
            + json.dumps(_review_lessons(config), ensure_ascii=False, sort_keys=True)
        )
        return 0

    def approve_lesson(args: argparse.Namespace) -> int:
        try:
            lesson = _approve_lesson(config, args.approve_lesson)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print("lesson: " + json.dumps(lesson, ensure_ascii=False, sort_keys=True))
        return 0

    def revoke_lesson(args: argparse.Namespace) -> int:
        lesson_id, reason = args.revoke_lesson
        try:
            lesson = _lesson_store(config).revoke(lesson_id, reason)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            "lesson: "
            + json.dumps(lesson.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        return 0

    def supersede_lesson(args: argparse.Namespace) -> int:
        lesson_id, text = args.supersede_lesson
        try:
            lesson = _lesson_store(config).supersede(lesson_id, text)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            "lesson: "
            + json.dumps(lesson.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        return 0

    context = CapabilityAssemblyContext(
        {
            "cli_handlers": {
                "dream": dream,
                "rebuild_vec": rebuild_vec,
                "distill": distill,
                "review_lessons": review_lessons,
                "approve_lesson": approve_lesson,
                "revoke_lesson": revoke_lesson,
                "supersede_lesson": supersede_lesson,
            }
        }
    )
    return assemble_capabilities(build_capability_registry(config), context)


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.task and not _maintenance_requested(args):
        parser.error("a task description is required")
    if args.task and _maintenance_requested(args):
        parser.error("task description cannot be combined with a maintenance command")

    config = V2Config.from_env(_overrides_from_args(args))
    command_name = _maintenance_command(args)
    if command_name is not None:
        handler = _build_cli_capability_context(config).cli_commands.get(command_name)
        if handler is None:
            print(
                f"error: capability for --{command_name.replace('_', '-')} is unavailable",
                file=sys.stderr,
            )
            return 1
        return int(handler(args))

    # ThinPhoneAgent is delivered by the integration/middleware workstream
    # (phone_agent/v2/agent.py). Guard the import so the CLI skeleton lands and
    # runs --help before that file exists.
    try:
        from phone_agent.v2.agent import ThinPhoneAgent
    except ImportError as exc:
        print(
            "error: phone_agent.v2.agent.ThinPhoneAgent is not available yet "
            f"(this lands at integration): {exc}",
            file=sys.stderr,
        )
        return 1

    agent = ThinPhoneAgent(config)
    result = agent.run(args.task)
    if getattr(agent, "_last_dream_summary", None) is not None:
        _print_dream_summary(agent._last_dream_summary)
    elif config.app_kb_enabled and config.dream_mode == "auto":
        # Compatibility for injected/legacy agent factories without lifecycle
        # hooks.  ThinPhoneAgent itself executes this through the dream run_end
        # hook and therefore never enters this branch.
        _print_dream_summary(
            _run_dream(
                config, light=True, store=getattr(agent.session, "app_store", None)
            )
        )

    steps = getattr(result, "steps", None)
    reason = getattr(result, "reason", "")
    success = bool(getattr(result, "success", False))
    trace_path = getattr(result, "trace_path", None)

    print(f"steps={steps} reason={reason}")
    if trace_path:
        print(f"trace: {trace_path}")

    if success:
        return 0
    if reason in {"token_budget_exhausted", "loop_fuse"}:
        return 3
    if getattr(result, "reason", None) and "takeover" in str(reason).lower():
        return 2
    if getattr(result, "takeover_reason", None):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
