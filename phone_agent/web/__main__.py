"""CLI entry point for ``python -m phone_agent.web``."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m phone_agent.web",
        description="Open-AutoGLM thin-loop 本地实时控制台",
    )
    parser.add_argument("--device-id", default=None, help="ADB 设备序列号")
    parser.add_argument("--model", default=None, help="模型 ID（PHONE_AGENT_MODEL）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from phone_agent.web.app import run

    run(device_id=args.device_id, model=args.model, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
