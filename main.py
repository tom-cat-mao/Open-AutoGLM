#!/usr/bin/env python3
"""
Phone Agent CLI - AI-powered Android phone automation.

Usage:
    python main.py [OPTIONS]

Environment Variables:
    PHONE_AGENT_BASE_URL: Model API base URL (default: http://localhost:8000/v1)
    PHONE_AGENT_MODEL: Model name (default: autoglm-phone-9b)
    PHONE_AGENT_API_KEY: API key for model authentication (default: EMPTY)
    PHONE_AGENT_HTTP_HEADERS: Extra model API headers as JSON or Header=Value entries
    PHONE_AGENT_USER_AGENT: Optional User-Agent header for model API requests
    PHONE_AGENT_CF_ACCESS_CLIENT_ID: Cloudflare Access service token client id
    PHONE_AGENT_CF_ACCESS_CLIENT_SECRET: Cloudflare Access service token client secret
    PHONE_AGENT_MODEL_TIMEOUT: Model API timeout in seconds (default: 60)
    PHONE_AGENT_MODEL_MAX_RETRIES: Model API max retries (default: 2)
    PHONE_AGENT_STREAM: Enable streaming model responses when true (default: false)
    PHONE_AGENT_MODEL_EXTRA_BODY: Extra JSON fields for model request body
    PHONE_AGENT_THINKING_MODE: Thinking mode control: auto, on, off (default: auto)
    PHONE_AGENT_THINKING_PARAM: Thinking parameter style: enable_thinking or chat_template_kwargs
    PHONE_AGENT_SCREENSHOT_FORMAT: Screenshot payload format, jpeg or png (default: jpeg)
    PHONE_AGENT_SCREENSHOT_JPEG_QUALITY: JPEG quality for model screenshot payload (default: 80)
    PHONE_AGENT_SKIP_MODEL_CHECK: Skip startup model API check when true
    PHONE_AGENT_OUTPUT_MODE: Model output mode (json_schema/tool_calls/auto)
    PHONE_AGENT_MAX_STEPS: Maximum steps per task (default: 100)
    PHONE_AGENT_DEVICE_ID: ADB device ID for multi-device setups
    PHONE_AGENT_GROUNDING_PROVIDER: Mark provider (off/fake/locateanything/hybrid)
    PHONE_AGENT_ACCESSIBILITY_MARKS: Include UiAutomator marks as device screen marks
    PHONE_AGENT_ACCESSIBILITY_MAX_MARKS: Maximum UiAutomator marks per screen
    PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS: Optional short LocateAnything context budget
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_MODEL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Open-AutoGLM/0.1"
)
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "api-key",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "cf-access-client-secret",
}
SENSITIVE_HEADER_MARKERS = (
    "auth",
    "credential",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
)
SENSITIVE_URL_QUERY_MARKERS = ("api_key", "apikey", "auth", "key", "password", "secret", "token")
BLOCKED_HEADER_NAMES = {
    "connection",
    "content-length",
    "host",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def redact_url_for_display(url: str) -> str:
    """Redact URL credentials and sensitive query parameters before logging."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted-url>"

    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username or parts.password:
        netloc = f"<redacted>@{netloc}"

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_URL_QUERY_MARKERS):
            query_items.append((key, "<redacted>"))
        else:
            query_items.append((key, value))
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query_items, safe="<>"), parts.fragment))


def load_dotenv(
    path: str | None = None,
    allowed_prefixes: Iterable[str] = ("PHONE_AGENT_",),
) -> None:
    """Load simple project .env KEY=VALUE pairs without overriding the shell."""
    path = path or os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if (
                not key
                or key in os.environ
                or not any(key.startswith(prefix) for prefix in allowed_prefixes)
            ):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value


from openai import OpenAI

from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.config.apps import list_supported_apps
from phone_agent.device_factory import DeviceType, get_device_factory, set_device_type
from phone_agent.model import ModelConfig


def validate_header(name: str, value: str) -> tuple[str, str]:
    """Validate user-provided HTTP header name/value before passing to httpx."""
    name = name.strip()
    value = value.strip()
    if not name:
        raise ValueError("HTTP header name must not be empty")
    if name.lower() in BLOCKED_HEADER_NAMES:
        raise ValueError(f"HTTP header is not allowed: {name}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name + value):
        raise ValueError(f"HTTP header contains control characters: {name}")
    return name, value


def redact_sensitive_text(text: str, headers: dict[str, str] | None = None) -> str:
    """Redact configured secrets from user-visible diagnostic errors."""
    redacted = text
    for secret in (os.getenv("PHONE_AGENT_API_KEY"), os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET")):
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for name, value in (headers or {}).items():
        lowered = name.lower()
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def redact_api_error(
    text: str,
    api_key: str | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """Redact API key and sensitive headers from diagnostic exceptions."""
    redacted = redact_sensitive_text(text, headers)
    if api_key and api_key != "EMPTY":
        redacted = redacted.replace(api_key, "[REDACTED]")
    return redacted


def parse_env_headers(value: str | None) -> dict[str, str]:
    """Parse HTTP headers from JSON or newline/comma separated KEY=VALUE env text."""
    if not value:
        return {}

    stripped = value.strip()
    if not stripped:
        return {}

    if stripped.startswith("{"):
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("PHONE_AGENT_HTTP_HEADERS JSON must be an object")
        return dict(validate_header(str(k), str(v)) for k, v in parsed.items())

    headers: dict[str, str] = {}
    for item in stripped.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            key, header_value = item.split(":", 1)
        elif "=" in item:
            key, header_value = item.split("=", 1)
        else:
            raise ValueError(
                "PHONE_AGENT_HTTP_HEADERS entries must use Header=Value or Header:Value"
            )
        key, header_value = validate_header(key, header_value)
        headers[key] = header_value
    return headers


def parse_env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean environment variables used by CLI flags."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_json_object(value: str | None, name: str) -> dict[str, Any]:
    """Parse a JSON object option from CLI/env text."""
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def build_model_extra_body(
    extra_body: dict[str, Any],
    thinking_mode: str = "auto",
    thinking_param: str = "enable_thinking",
) -> dict[str, Any]:
    """Merge provider-specific model request body options for diagnosis output."""
    merged = dict(extra_body)
    if thinking_mode == "auto":
        return merged

    enable_thinking = thinking_mode == "on"
    if thinking_param == "chat_template_kwargs":
        chat_template_kwargs = dict(merged.get("chat_template_kwargs") or {})
        chat_template_kwargs["enable_thinking"] = enable_thinking
        merged["chat_template_kwargs"] = chat_template_kwargs
    else:
        merged["enable_thinking"] = enable_thinking
    return merged


def build_model_headers(args: argparse.Namespace) -> dict[str, str]:
    """Build OpenAI-compatible client headers from CLI/env configuration."""
    headers = parse_env_headers(os.getenv("PHONE_AGENT_HTTP_HEADERS"))

    user_agent = (
        args.user_agent or os.getenv("PHONE_AGENT_USER_AGENT") or DEFAULT_MODEL_USER_AGENT
    )
    if user_agent:
        key, value = validate_header("User-Agent", user_agent)
        headers[key] = value

    cf_client_id = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID")
    cf_client_secret = os.getenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET")
    if bool(cf_client_id) != bool(cf_client_secret):
        raise ValueError(
            "PHONE_AGENT_CF_ACCESS_CLIENT_ID and PHONE_AGENT_CF_ACCESS_CLIENT_SECRET "
            "must be configured together"
        )
    if cf_client_id and cf_client_secret:
        key, value = validate_header("CF-Access-Client-Id", cf_client_id)
        headers[key] = value
        key, value = validate_header("CF-Access-Client-Secret", cf_client_secret)
        headers[key] = value

    return headers


def check_system_requirements() -> bool:
    """
    Check system requirements before running the agent.

    Checks:
    1. ADB installed
    2. At least one device connected
    3. ADB Keyboard installed on the device

    Returns:
        True if all checks pass, False otherwise.
    """
    print("🔍 Checking system requirements...")
    print("-" * 50)

    all_passed = True

    # Check 1: ADB installed
    print("1. Checking ADB installation...", end=" ")
    if shutil.which("adb") is None:
        print("❌ FAILED")
        print("   Error: ADB is not installed or not in PATH.")
        print("   Solution: Install ADB:")
        print("     - macOS: brew install android-platform-tools")
        print("     - Linux: sudo apt install android-tools-adb")
        print(
            "     - Windows: Download from https://developer.android.com/studio/releases/platform-tools"
        )
        all_passed = False
    else:
        try:
            result = subprocess.run(
                ["adb", "version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version_line = result.stdout.strip().split("\n")[0]
                print(f"✅ OK ({version_line})")
            else:
                print("❌ FAILED")
                print("   Error: ADB command failed to run.")
                all_passed = False
        except FileNotFoundError:
            print("❌ FAILED")
            print("   Error: ADB command not found.")
            all_passed = False
        except subprocess.TimeoutExpired:
            print("❌ FAILED")
            print("   Error: ADB command timed out.")
            all_passed = False

    if not all_passed:
        print("-" * 50)
        print("❌ System check failed. Please fix the issues above.")
        return False

    # Check 2: Device connected
    print("2. Checking connected devices...", end=" ")
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split("\n")
        devices = [
            line for line in lines[1:] if line.strip() and "\tdevice" in line
        ]

        if not devices:
            print("❌ FAILED")
            print("   Error: No devices connected.")
            print("   Solution:")
            print("     1. Enable USB debugging on your Android device")
            print("     2. Connect via USB and authorize the connection")
            print(
                "     3. Or connect remotely: python main.py --connect <ip>:<port>"
            )
            all_passed = False
        else:
            device_ids = [d.split("\t")[0] for d in devices]
            print(
                f"✅ OK ({len(devices)} device(s): {', '.join(device_ids[:2])}{'...' if len(device_ids) > 2 else ''})"
            )
    except subprocess.TimeoutExpired:
        print("❌ FAILED")
        print("   Error: ADB command timed out.")
        all_passed = False
    except Exception as e:
        print("❌ FAILED")
        print(f"   Error: {e}")
        all_passed = False

    if not all_passed:
        print("-" * 50)
        print("❌ System check failed. Please fix the issues above.")
        return False

    # Check 3: ADB Keyboard installed
    print("3. Checking ADB Keyboard...", end=" ")
    try:
        result = subprocess.run(
            ["adb", "shell", "ime", "list", "-s"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ime_list = result.stdout.strip()

        if "com.android.adbkeyboard/.AdbIME" in ime_list:
            print("✅ OK")
        else:
            print("❌ FAILED")
            print("   Error: ADB Keyboard is not installed on the device.")
            print("   Solution:")
            print("     1. Download ADB Keyboard APK from:")
            print(
                "        https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk"
            )
            print("     2. Install it on your device: adb install ADBKeyboard.apk")
            print(
                "     3. Enable it in Settings > System > Languages & Input > Virtual Keyboard"
            )
            all_passed = False
    except subprocess.TimeoutExpired:
        print("❌ FAILED")
        print("   Error: ADB command timed out.")
        all_passed = False
    except Exception as e:
        print("❌ FAILED")
        print(f"   Error: {e}")
        all_passed = False

    print("-" * 50)

    if all_passed:
        print("✅ All system checks passed!\n")
    else:
        print("❌ System check failed. Please fix the issues above.")

    return all_passed


def check_model_api(base_url: str, model_name: str, api_key: str = "EMPTY") -> bool:
    """
    Check if the model API is accessible and the specified model exists.

    Args:
        base_url: The API base URL
        model_name: The model name to check
        api_key: The API key for authentication

    Returns:
        True if all checks pass, False otherwise.
    """
    print("🔍 Checking model API...")
    print("-" * 50)
    display_base_url = redact_url_for_display(base_url)

    all_passed = True

    print(f"1. Checking API connectivity ({display_base_url})...", end=" ")
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)

        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            temperature=0.0,
            stream=False,
        )

        if response.choices and len(response.choices) > 0:
            print("✅ OK")
        else:
            print("❌ FAILED")
            print("   Error: Received empty response from API")
            all_passed = False

    except Exception as e:
        print("❌ FAILED")
        error_msg = redact_api_error(str(e), api_key=api_key)
        if base_url != display_base_url:
            error_msg = error_msg.replace(base_url, display_base_url)

        if "Connection refused" in error_msg or "Connection error" in error_msg:
            print(f"   Error: Cannot connect to {display_base_url}")
            print("   Solution:")
            print("     1. Check if the model server is running")
            print("     2. Verify the base URL is correct")
            print(f"     3. Try: curl {display_base_url}/chat/completions")
        elif "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
            print(f"   Error: Connection to {display_base_url} timed out")
            print("   Solution:")
            print("     1. Check your network connection")
            print("     2. Verify the server is responding")
        elif (
            "Name or service not known" in error_msg
            or "nodename nor servename" in error_msg
        ):
            print(f"   Error: Cannot resolve hostname")
            print("   Solution:")
            print("     1. Check the URL is correct")
            print("     2. Verify DNS settings")
        else:
            print(f"   Error: {error_msg}")

        all_passed = False

    print("-" * 50)

    if all_passed:
        print("✅ Model API checks passed!\n")
    else:
        print("❌ Model API check failed. Please fix the issues above.")

    return all_passed


def diagnose_model_api(
    base_url: str,
    model_name: str,
    api_key: str = "EMPTY",
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_retries: int = 2,
    stream: bool = False,
    extra_body: dict[str, Any] | None = None,
) -> bool:
    """Check model API with the same headers used by runtime requests."""
    print("🔍 Diagnosing model API...")
    print("-" * 50)
    print(f"Base URL: {redact_url_for_display(base_url)}")
    print(f"Model: {model_name}")
    if headers:
        print(f"Custom headers: {', '.join(sorted(headers))}")
    print(f"Stream: {'enabled' if stream else 'disabled'}")
    if extra_body:
        print(f"Extra body keys: {', '.join(sorted(extra_body))}")

    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=headers or None,
        )
        request_kwargs = {
            "model": model_name,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
            "temperature": 0.0,
            "stream": stream,
            "extra_body": extra_body or {},
        }
        response = client.chat.completions.create(**request_kwargs)
        if stream:
            has_content = False
            for chunk in response:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None) or getattr(delta, "reasoning_content", None):
                    has_content = True
                    break
            if has_content:
                print("✅ Model API diagnosis passed")
                return True
            print("❌ Model API diagnosis failed: empty stream")
            return False

        if response.choices:
            print("✅ Model API diagnosis passed")
            return True
        print("❌ Model API diagnosis failed: empty response")
        return False
    except Exception as e:
        error_msg = redact_api_error(str(e), api_key=api_key, headers=headers)
        print(f"❌ Model API diagnosis failed: {error_msg}")
        lowered = error_msg.lower()
        if (
            "cloudflare" in lowered
            or "cf-" in lowered
            or "blocked" in lowered
            or "403" in error_msg
            or "1020" in error_msg
        ):
            print("   可能被 Cloudflare/WAF 拦截。可选方案：")
            print("     1. 如果使用 Cloudflare Access，配置 PHONE_AGENT_CF_ACCESS_CLIENT_ID/SECRET")
            print("     2. 配置 PHONE_AGENT_HTTP_HEADERS 添加中转站要求的鉴权/白名单 Header")
            print("     3. 在 Cloudflare 为 /v1/chat/completions 创建 Skip/BYPASS WAF 规则或服务 Token")
            print("     4. 将当前出口 IP 加入允许列表，避免依赖浏览器挑战页")
        return False


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Phone Agent - AI-powered Android phone automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run with default settings
    python main.py

    # Specify model endpoint
    python main.py --base-url http://localhost:8000/v1

    # Use API key for authentication
    python main.py --apikey sk-xxxxx

    # Run with specific device
    python main.py --device-id emulator-5554

    # Connect to remote device
    python main.py --connect 192.168.1.100:5555

    # List connected devices
    python main.py --list-devices

    # Enable TCP/IP on USB device and get connection info
    python main.py --enable-tcpip

    # List supported apps
    python main.py --list-apps
        """,
    )

    # Model options
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
        help="Model API base URL",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
        help="Model name",
    )

    parser.add_argument(
        "--apikey",
        type=str,
        default=os.getenv("PHONE_AGENT_API_KEY", "EMPTY"),
        help="API key for model authentication",
    )

    parser.add_argument(
        "--user-agent",
        type=str,
        default=os.getenv("PHONE_AGENT_USER_AGENT"),
        help="Optional User-Agent header for model API requests",
    )

    parser.add_argument(
        "--model-timeout",
        type=float,
        default=float(os.getenv("PHONE_AGENT_MODEL_TIMEOUT", "60")),
        help="Model API timeout in seconds",
    )

    parser.add_argument(
        "--model-max-retries",
        type=int,
        default=int(os.getenv("PHONE_AGENT_MODEL_MAX_RETRIES", "2")),
        help="Model API max retries",
    )

    parser.add_argument(
        "--stream",
        action="store_true",
        default=parse_env_bool("PHONE_AGENT_STREAM", False),
        help="Enable streaming model responses",
    )

    parser.add_argument(
        "--model-extra-body",
        type=str,
        default=os.getenv("PHONE_AGENT_MODEL_EXTRA_BODY"),
        help='Extra JSON object merged into model API request body, e.g. \'{"enable_thinking":false}\'',
    )

    parser.add_argument(
        "--thinking-mode",
        type=str,
        choices=["auto", "on", "off"],
        default=os.getenv("PHONE_AGENT_THINKING_MODE", "auto"),
        help="Provider thinking mode control: auto, on, or off",
    )

    parser.add_argument(
        "--thinking-param",
        type=str,
        choices=["enable_thinking", "chat_template_kwargs"],
        default=os.getenv("PHONE_AGENT_THINKING_PARAM", "enable_thinking"),
        help="How to pass thinking mode: enable_thinking for DashScope/Qwen API, chat_template_kwargs for vLLM/SGLang",
    )

    parser.add_argument(
        "--skip-model-check",
        action="store_true",
        default=parse_env_bool("PHONE_AGENT_SKIP_MODEL_CHECK", False),
        help="Skip startup model API check",
    )

    parser.add_argument(
        "--diagnose-model-api",
        action="store_true",
        help="Run model API diagnosis with configured headers and exit",
    )

    parser.add_argument(
        "--output-mode",
        type=str,
        choices=["json_schema", "tool_calls", "auto"],
        default=os.getenv("PHONE_AGENT_OUTPUT_MODE", "json_schema"),
        help="Model output mode: json_schema, tool_calls, or auto",
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=int(os.getenv("PHONE_AGENT_MAX_STEPS", "100")),
        help="Maximum steps per task",
    )

    parser.add_argument(
        "--grounding-provider",
        type=str,
        choices=["off", "fake", "locateanything", "locateanything_mlx", "mlx", "accessibility", "accessibility_tree", "uiautomator", "hybrid"],
        default=os.getenv("PHONE_AGENT_GROUNDING_PROVIDER"),
        help="Optional mark provider: off, fake, locateanything, accessibility, or hybrid",
    )

    parser.add_argument(
        "--accessibility-marks",
        action="store_true",
        default=parse_env_bool("PHONE_AGENT_ACCESSIBILITY_MARKS", False),
        help="Include Android UiAutomator accessibility marks as device screen marks",
    )

    parser.add_argument(
        "--accessibility-timeout",
        type=float,
        default=float(os.getenv("PHONE_AGENT_ACCESSIBILITY_TIMEOUT", "3.0")),
        help="UiAutomator dump timeout in seconds",
    )

    parser.add_argument(
        "--accessibility-max-marks",
        type=int,
        default=int(os.getenv("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS", "80")),
        help="Maximum UiAutomator marks per screen",
    )

    parser.add_argument(
        "--locateanything-context-max-chars",
        type=int,
        default=int(os.getenv("PHONE_AGENT_LOCATEANYTHING_CONTEXT_MAX_CHARS", "0")),
        help="Optional short context budget for LocateAnything prompts; 0 disables extra context",
    )

    # Device options
    parser.add_argument(
        "--device-id",
        "-d",
        type=str,
        default=os.getenv("PHONE_AGENT_DEVICE_ID"),
        help="ADB device ID",
    )

    parser.add_argument(
        "--connect",
        "-c",
        type=str,
        metavar="ADDRESS",
        help="Connect to remote device (e.g., 192.168.1.100:5555)",
    )

    parser.add_argument(
        "--disconnect",
        type=str,
        nargs="?",
        const="all",
        metavar="ADDRESS",
        help="Disconnect from remote device (or 'all' to disconnect all)",
    )

    parser.add_argument(
        "--list-devices", action="store_true", help="List connected devices and exit"
    )

    parser.add_argument(
        "--enable-tcpip",
        type=int,
        nargs="?",
        const=5555,
        metavar="PORT",
        help="Enable TCP/IP debugging on USB device (default port: 5555)",
    )

    # Other options
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress verbose output"
    )

    parser.add_argument(
        "--list-apps", action="store_true", help="List supported apps and exit"
    )

    parser.add_argument(
        "--lang",
        type=str,
        choices=["cn", "en"],
        default=os.getenv("PHONE_AGENT_LANG", "cn"),
        help="Language for system prompt (cn or en, default: cn)",
    )

    parser.add_argument(
        "task",
        nargs="?",
        type=str,
        help="Task to execute (interactive mode if not provided)",
    )

    args = parser.parse_args()
    if args.output_mode not in {"json_schema", "tool_calls", "auto"}:
        parser.error("--output-mode must be one of: json_schema, tool_calls, auto")
    if args.model_timeout <= 0:
        parser.error("--model-timeout must be positive")
    if args.model_max_retries < 0:
        parser.error("--model-max-retries must be non-negative")
    try:
        args.model_extra_body_dict = parse_json_object(
            args.model_extra_body, "--model-extra-body / PHONE_AGENT_MODEL_EXTRA_BODY"
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def handle_device_commands(args) -> bool:
    """
    Handle device-related commands.

    Returns:
        True if a device command was handled (should exit), False otherwise.
    """
    device_factory = get_device_factory()
    ConnectionClass = device_factory.get_connection_class()
    conn = ConnectionClass()

    # Handle --list-devices
    if args.list_devices:
        devices = device_factory.list_devices()
        if not devices:
            print("No devices connected.")
        else:
            print("Connected devices:")
            print("-" * 60)
            for device in devices:
                status_icon = "✓" if device.status == "device" else "✗"
                conn_type = device.connection_type.value
                model_info = f" ({device.model})" if device.model else ""
                print(
                    f"  {status_icon} {device.device_id:<30} [{conn_type}]{model_info}"
                )
        return True

    # Handle --connect
    if args.connect:
        print(f"Connecting to {args.connect}...")
        success, message = conn.connect(args.connect)
        print(f"{'✓' if success else '✗'} {message}")
        if success:
            args.device_id = args.connect
        return not success

    # Handle --disconnect
    if args.disconnect:
        if args.disconnect == "all":
            print("Disconnecting all remote devices...")
            success, message = conn.disconnect()
        else:
            print(f"Disconnecting from {args.disconnect}...")
            success, message = conn.disconnect(args.disconnect)
        print(f"{'✓' if success else '✗'} {message}")
        return True

    # Handle --enable-tcpip
    if args.enable_tcpip:
        port = args.enable_tcpip
        print(f"Enabling TCP/IP debugging on port {port}...")

        success, message = conn.enable_tcpip(port, args.device_id)
        print(f"{'✓' if success else '✗'} {message}")

        if success:
            ip = conn.get_device_ip(args.device_id)
            if ip:
                print(f"\nYou can now connect remotely using:")
                print(f"  python main.py --connect {ip}:{port}")
                print(f"\nOr via ADB directly:")
                print(f"  adb connect {ip}:{port}")
            else:
                print("\nCould not determine device IP. Check device WiFi settings.")
        return True

    return False


def main():
    """Main entry point."""
    load_dotenv()
    args = parse_args()
    try:
        model_headers = build_model_headers(args)
    except ValueError as e:
        print(f"Invalid model API header configuration: {e}")
        sys.exit(2)

    set_device_type(DeviceType.ADB)

    if args.diagnose_model_api:
        effective_extra_body = build_model_extra_body(
            args.model_extra_body_dict,
            args.thinking_mode,
            args.thinking_param,
        )
        if diagnose_model_api(
            args.base_url,
            args.model,
            args.apikey,
            headers=model_headers,
            timeout=args.model_timeout,
            max_retries=args.model_max_retries,
            stream=args.stream,
            extra_body=effective_extra_body,
        ):
            return
        sys.exit(1)

    # Handle --list-apps
    if args.list_apps:
        print("Supported Android apps:")
        for app in sorted(list_supported_apps()):
            print(f"  - {app}")
        return

    # Handle device commands
    if handle_device_commands(args):
        return

    # Run system requirements check
    if not check_system_requirements():
        sys.exit(1)

    # Check model API
    if not args.skip_model_check:
        if model_headers:
            effective_extra_body = build_model_extra_body(
                args.model_extra_body_dict,
                args.thinking_mode,
                args.thinking_param,
            )
            if not diagnose_model_api(
                args.base_url,
                args.model,
                args.apikey,
                headers=model_headers,
                timeout=args.model_timeout,
                max_retries=args.model_max_retries,
                stream=args.stream,
                extra_body=effective_extra_body,
            ):
                sys.exit(1)
        elif not check_model_api(args.base_url, args.model, args.apikey):
            sys.exit(1)

    # Create configurations
    model_config = ModelConfig(
        base_url=args.base_url,
        model_name=args.model,
        api_key=args.apikey,
        timeout=args.model_timeout,
        max_retries=args.model_max_retries,
        default_headers=model_headers,
        stream=args.stream,
        extra_body=args.model_extra_body_dict,
        thinking_mode=args.thinking_mode,
        thinking_param=args.thinking_param,
        lang=args.lang,
        output_mode=args.output_mode,
    )

    agent_config = AgentConfig(
        max_steps=args.max_steps,
        device_id=args.device_id,
        verbose=not args.quiet,
        lang=args.lang,
        grounding_provider_name=args.grounding_provider,
        accessibility_marks=args.accessibility_marks,
        accessibility_timeout=args.accessibility_timeout,
        accessibility_max_marks=args.accessibility_max_marks,
        locateanything_context_max_chars=args.locateanything_context_max_chars,
    )

    agent = PhoneAgent(
        model_config=model_config,
        agent_config=agent_config,
    )

    # Print header
    print("=" * 50)
    print("Phone Agent - AI-powered Android phone automation")
    print("=" * 50)
    print(f"Model: {model_config.model_name}")
    print(f"Base URL: {redact_url_for_display(model_config.base_url)}")
    print(f"Max Steps: {agent_config.max_steps}")
    print(f"Language: {agent_config.lang}")
    print(f"Device Type: ADB")
    print(f"Stream: {'enabled' if model_config.stream else 'disabled'}")
    print(f"Thinking mode: {model_config.thinking_mode}")

    # Show device info
    device_factory = get_device_factory()
    devices = device_factory.list_devices()
    if agent_config.device_id:
        print(f"Device: {agent_config.device_id}")
    elif devices:
        print(f"Device: {devices[0].device_id} (auto-detected)")

    print("=" * 50)

    # Run with provided task or enter interactive mode
    if args.task:
        print(f"\nTask: {args.task}\n")
        result = agent.run(args.task)
        print(f"\nResult: {result}")
    else:
        print("\nEntering interactive mode. Type 'quit' to exit.\n")

        while True:
            try:
                task = input("Enter your task: ").strip()

                if task.lower() in ("quit", "exit", "q"):
                    print("Goodbye!")
                    break

                if not task:
                    continue

                print()
                result = agent.run(task)
                print(f"\nResult: {result}\n")
                agent.reset()

            except KeyboardInterrupt:
                print("\n\nInterrupted. Goodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
