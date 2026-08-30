#!/usr/bin/env python
"""v2 CLI entry point for the thin-loop PhoneAgent.

Usage:
    .venv/bin/python main_v2.py "task description" --device-id X --max-steps 20 \
        --model M --base-url U --grounding-provider hybrid --lang cn --trace-dir .traces

Resolution order: CLI overrides > shell env > project .env > defaults. All flags
default to ``None`` so unset flags never clobber env-derived values.

Exit codes: success 0 / error 1 / takeover 2 / budget-or-fuse exhausted 3.

See ``docs/refactor-thin-loop-v2.md`` §11 for the binding contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

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
    parser.add_argument(
        "--dream",
        action="store_true",
        help="consolidate the local App-KB instead of running a phone task",
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
    """Maintain App-KB and the rule-based experience library."""

    summary: dict[str, Any] = {}
    try:
        from phone_agent.v2.appkb import AppKnowledgeStore
        from phone_agent.v2.dream import consolidate

        active_store = (
            store if store is not None else AppKnowledgeStore(config.memory_dir)
        )
        summary.update(
            consolidate(active_store, inventory=_device_inventory(config), light=light)
        )
    except Exception as exc:  # noqa: BLE001 - maintenance never masks run outcome
        summary = {"status": "skipped", "reason": type(exc).__name__}

    if getattr(config, "experience_enabled", False):
        try:
            from phone_agent.v2.dream import maintain_experience

            summary["experience"] = maintain_experience(
                getattr(config, "experience_dir", "memory/experience"),
                keep=getattr(config, "episode_keep", 500),
                archive_days=getattr(config, "episode_archive_days", 90),
            )
        except Exception as exc:  # noqa: BLE001 - maintenance never masks run outcome
            summary["experience"] = {
                "status": "skipped",
                "reason": type(exc).__name__,
            }
    return summary


def _print_dream_summary(summary: dict[str, Any]) -> None:
    print(f"dream: {json.dumps(summary, ensure_ascii=False, sort_keys=True)}")


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.task and not args.dream:
        parser.error("a task description is required")

    config = V2Config.from_env(_overrides_from_args(args))
    if args.dream:
        _print_dream_summary(_run_dream(config, light=False))
        return 0

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
    if config.app_kb_enabled and config.dream_mode == "auto":
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
