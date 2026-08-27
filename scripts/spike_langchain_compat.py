"""Spike: verify the configured OpenAI-compatible gateway works with LangChain
ChatOpenAI + tool_calls + multimodal (image) messages + custom sampling params.

All configuration comes from PHONE_AGENT_* env vars / project .env (same
resolution order as the rest of the project). Nothing is hardcoded.

Usage:
    .venv/bin/python scripts/spike_langchain_compat.py [--with-device-screenshot]

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_project_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("PHONE_AGENT_") or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def env_float(key: str) -> float | None:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return None
    return float(raw)


DEFAULT_MODEL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Open-AutoGLM/0.1"
)


def build_default_headers() -> dict[str, str]:
    """Mirror main.py build_model_headers: UA + PHONE_AGENT_HTTP_HEADERS + CF Access."""

    headers: dict[str, str] = {}
    raw = os.getenv("PHONE_AGENT_HTTP_HEADERS")
    if raw:
        for pair in raw.split(";"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    headers.setdefault(
        "User-Agent", os.getenv("PHONE_AGENT_USER_AGENT") or DEFAULT_MODEL_USER_AGENT
    )
    cf_id = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID")
    cf_secret = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret
    return headers


def capture_device_screenshot(device_id: str) -> bytes:
    result = subprocess.run(
        ["adb", "-s", device_id, "exec-out", "screencap", "-p"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-device-screenshot", action="store_true")
    args = parser.parse_args()

    load_project_env()

    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    base_url = os.environ["PHONE_AGENT_BASE_URL"]
    model_name = os.environ["PHONE_AGENT_MODEL"]
    api_key = os.environ.get("PHONE_AGENT_API_KEY", "EMPTY")

    sampling = {}
    for env_key, param in (
        ("PHONE_AGENT_TEMPERATURE", "temperature"),
        ("PHONE_AGENT_TOP_P", "top_p"),
        ("PHONE_AGENT_FREQUENCY_PENALTY", "frequency_penalty"),
    ):
        value = env_float(env_key)
        if value is not None:
            sampling[param] = value

    print(f"model={model_name} base_url={base_url} sampling={sampling}")

    llm = ChatOpenAI(
        base_url=base_url,
        model=model_name,
        api_key=api_key,
        timeout=float(os.getenv("PHONE_AGENT_MODEL_TIMEOUT", "180")),
        max_retries=0,
        default_headers=build_default_headers(),
        **sampling,
    )

    @tool
    def tap(target_mark_id: str) -> str:
        """Tap a UI element by its mark id from the current screen marks."""

        return f"tapped {target_mark_id}"

    @tool
    def finish(reason: str) -> str:
        """Declare the task finished with a reason."""

        return f"finished: {reason}"

    checks: list[tuple[str, bool, str]] = []

    # Check 1: plain-text tool calling
    try:
        llm_with_tools = llm.bind_tools([tap, finish])
        response = llm_with_tools.invoke(
            [
                HumanMessage(
                    content=(
                        "You are a phone agent. The screen marks are: ax_1 = 'WLAN'. "
                        "Call the tap tool to open WLAN settings."
                    )
                )
            ]
        )
        tool_calls = getattr(response, "tool_calls", None) or []
        ok = bool(tool_calls) and tool_calls[0]["name"] == "tap"
        checks.append(("tool_calls_text", ok, json.dumps(tool_calls, ensure_ascii=False)[:200]))
    except Exception as exc:
        checks.append(("tool_calls_text", False, f"{type(exc).__name__}: {exc}"))

    # Check 2: image content block + tool calling
    image_b64: str | None = None
    if args.with_device_screenshot:
        device_id = os.environ.get("PHONE_AGENT_DEVICE_ID", "")
        try:
            png = capture_device_screenshot(device_id)
            image_b64 = base64.b64encode(png).decode()
            print(f"device screenshot: {len(png)} bytes from {device_id}")
        except Exception as exc:
            checks.append(("device_screenshot", False, f"{type(exc).__name__}: {exc}"))
    if image_b64 is None:
        # 1x1 red PNG fallback so the multimodal path is still exercised.
        image_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    try:
        llm_with_tools = llm.bind_tools([tap, finish])
        response = llm_with_tools.invoke(
            [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                "This is a phone screenshot. If you can see it, call finish "
                                "with a one-line description of what is on screen."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ]
                )
            ]
        )
        tool_calls = getattr(response, "tool_calls", None) or []
        ok = bool(tool_calls) and tool_calls[0]["name"] == "finish"
        detail = json.dumps(tool_calls, ensure_ascii=False)[:300] if tool_calls else str(response.content)[:300]
        checks.append(("tool_calls_image", ok, detail))
    except Exception as exc:
        checks.append(("tool_calls_image", False, f"{type(exc).__name__}: {exc}"))

    print()
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
