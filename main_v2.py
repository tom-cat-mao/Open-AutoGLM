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
import sys

from phone_agent.v2.config import V2Config, load_project_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main_v2.py",
        description="Open-AutoGLM thin-loop v2 PhoneAgent CLI",
    )
    parser.add_argument("task", nargs="?", default=None, help="natural-language task description")
    parser.add_argument("--device-id", default=None, help="ADB device serial")
    parser.add_argument("--max-steps", type=int, default=None, help="runaway-loop fuse: max model calls (PHONE_AGENT_MAX_STEPS, default 100)")
    parser.add_argument("--model", default=None, help="model id (PHONE_AGENT_MODEL)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--grounding-provider", default=None, help="grounding provider name")
    parser.add_argument("--lang", default=None, help="prompt language (cn/en)")
    parser.add_argument("--trace-dir", default=None, help="trace output directory")
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


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.task:
        parser.error("a task description is required")

    config = V2Config.from_env(_overrides_from_args(args))

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
