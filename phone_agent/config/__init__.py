"""Configuration module for Phone Agent."""

from phone_agent.config.app_registry import (
    AppIdentity,
    AppRegistry,
    AppResolution,
    ForegroundAppObservation,
    InstalledAppInventory,
    LaunchPolicy,
    LaunchTargetResolution,
    LaunchTargetResolver,
)
from phone_agent.config.apps import (
    APP_PACKAGES,
    DEFAULT_APP_REGISTRY,
    DEFAULT_LAUNCH_POLICY,
    DEFAULT_LAUNCH_TARGET_RESOLVER,
)
from phone_agent.config.i18n import get_message, get_messages
from phone_agent.config.prompts_en import (
    AUTO_OUTPUT_CONTRACT as AUTO_OUTPUT_CONTRACT_EN,
    BASE_SYSTEM_PROMPT as BASE_SYSTEM_PROMPT_EN,
    JSON_OUTPUT_CONTRACT as JSON_OUTPUT_CONTRACT_EN,
    SYSTEM_PROMPT as SYSTEM_PROMPT_EN,
    TOOL_CALLS_OUTPUT_CONTRACT as TOOL_CALLS_OUTPUT_CONTRACT_EN,
)
from phone_agent.config.prompts_zh import (
    AUTO_OUTPUT_CONTRACT as AUTO_OUTPUT_CONTRACT_ZH,
    BASE_SYSTEM_PROMPT as BASE_SYSTEM_PROMPT_ZH,
    JSON_OUTPUT_CONTRACT as JSON_OUTPUT_CONTRACT_ZH,
    SYSTEM_PROMPT as SYSTEM_PROMPT_ZH,
    TOOL_CALLS_OUTPUT_CONTRACT as TOOL_CALLS_OUTPUT_CONTRACT_ZH,
)
from phone_agent.config.timing import (
    TIMING_CONFIG,
    ActionTimingConfig,
    ConnectionTimingConfig,
    DeviceTimingConfig,
    TimingConfig,
    get_timing_config,
    update_timing_config,
)

PROMPT_VERSION = "context_harness_v1"
VALID_OUTPUT_MODES = {"json_schema", "tool_calls", "auto"}


def _output_contract(lang: str, output_mode: str) -> str:
    """Return the single output contract for the selected mode."""
    if output_mode not in VALID_OUTPUT_MODES:
        raise ValueError("output_mode must be one of: json_schema, tool_calls, auto")
    contracts = {
        "cn": {
            "json_schema": JSON_OUTPUT_CONTRACT_ZH,
            "tool_calls": TOOL_CALLS_OUTPUT_CONTRACT_ZH,
            "auto": AUTO_OUTPUT_CONTRACT_ZH,
        },
        "en": {
            "json_schema": JSON_OUTPUT_CONTRACT_EN,
            "tool_calls": TOOL_CALLS_OUTPUT_CONTRACT_EN,
            "auto": AUTO_OUTPUT_CONTRACT_EN,
        },
    }
    return contracts["en" if lang == "en" else "cn"][output_mode]


def get_prompt_version(prompt_version: str | None = None) -> str:
    """Normalize the prompt version label used by trace/eval metadata."""
    if prompt_version in {None, "", PROMPT_VERSION}:
        return PROMPT_VERSION
    raise ValueError(f"unsupported prompt_version: {prompt_version}")


def get_system_prompt(
    lang: str = "cn",
    output_mode: str = "json_schema",
    prompt_version: str | None = None,
) -> str:
    """
    Get system prompt by language, output mode, and prompt version.

    Args:
        lang: Language code, 'cn' for Chinese, 'en' for English.
        output_mode: Model output mode, one of json_schema/tool_calls/auto.
        prompt_version: Prompt renderer version. The default context_harness_v1 uses
            a single structured output contract per mode.

    Returns:
        System prompt string.
    """
    get_prompt_version(prompt_version)
    base_prompt = BASE_SYSTEM_PROMPT_EN if lang == "en" else BASE_SYSTEM_PROMPT_ZH
    return "\n\n".join([base_prompt, _output_contract(lang, output_mode)])


# Default to Chinese for backward compatibility. Active prompt rendering goes
# through get_system_prompt(); the legacy prompts.py rollback file was removed.
SYSTEM_PROMPT = SYSTEM_PROMPT_ZH

__all__ = [
    "APP_PACKAGES",
    "AppIdentity",
    "AppRegistry",
    "AppResolution",
    "ForegroundAppObservation",
    "InstalledAppInventory",
    "LaunchPolicy",
    "LaunchTargetResolution",
    "LaunchTargetResolver",
    "DEFAULT_APP_REGISTRY",
    "DEFAULT_LAUNCH_POLICY",
    "DEFAULT_LAUNCH_TARGET_RESOLVER",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_ZH",
    "SYSTEM_PROMPT_EN",
    "PROMPT_VERSION",
    "get_prompt_version",
    "get_system_prompt",
    "get_messages",
    "get_message",
    "TIMING_CONFIG",
    "TimingConfig",
    "ActionTimingConfig",
    "DeviceTimingConfig",
    "ConnectionTimingConfig",
    "get_timing_config",
    "update_timing_config",
]
