"""Configuration module for Phone Agent."""

from phone_agent.config.apps import APP_PACKAGES
from phone_agent.config.i18n import get_message, get_messages
from phone_agent.config.prompts_en import (
    AUTO_OUTPUT_CONTRACT as AUTO_OUTPUT_CONTRACT_EN,
    BASE_SYSTEM_PROMPT as BASE_SYSTEM_PROMPT_EN,
    JSON_OUTPUT_CONTRACT as JSON_OUTPUT_CONTRACT_EN,
    LEGACY_SYSTEM_PROMPT as LEGACY_SYSTEM_PROMPT_EN,
    SYSTEM_PROMPT as SYSTEM_PROMPT_EN,
    TEXT_DSL_OUTPUT_CONTRACT as TEXT_DSL_OUTPUT_CONTRACT_EN,
    TOOL_CALLS_OUTPUT_CONTRACT as TOOL_CALLS_OUTPUT_CONTRACT_EN,
)
from phone_agent.config.prompts_zh import (
    AUTO_OUTPUT_CONTRACT as AUTO_OUTPUT_CONTRACT_ZH,
    BASE_SYSTEM_PROMPT as BASE_SYSTEM_PROMPT_ZH,
    JSON_OUTPUT_CONTRACT as JSON_OUTPUT_CONTRACT_ZH,
    LEGACY_SYSTEM_PROMPT as LEGACY_SYSTEM_PROMPT_ZH,
    SYSTEM_PROMPT as SYSTEM_PROMPT_ZH,
    TEXT_DSL_OUTPUT_CONTRACT as TEXT_DSL_OUTPUT_CONTRACT_ZH,
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
LEGACY_PROMPT_VERSION = "legacy_text_dsl"
VALID_OUTPUT_MODES = {"text_dsl", "json_schema", "tool_calls", "auto"}


def _output_contract(lang: str, output_mode: str) -> str:
    """Return the single output contract for the selected mode."""
    if output_mode not in VALID_OUTPUT_MODES:
        raise ValueError(
            "output_mode must be one of: text_dsl, json_schema, tool_calls, auto"
        )
    contracts = {
        "cn": {
            "text_dsl": TEXT_DSL_OUTPUT_CONTRACT_ZH,
            "json_schema": JSON_OUTPUT_CONTRACT_ZH,
            "tool_calls": TOOL_CALLS_OUTPUT_CONTRACT_ZH,
            "auto": AUTO_OUTPUT_CONTRACT_ZH,
        },
        "en": {
            "text_dsl": TEXT_DSL_OUTPUT_CONTRACT_EN,
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
    if prompt_version == LEGACY_PROMPT_VERSION:
        return LEGACY_PROMPT_VERSION
    raise ValueError(f"unsupported prompt_version: {prompt_version}")


def get_system_prompt(
    lang: str = "cn",
    output_mode: str = "text_dsl",
    prompt_version: str | None = None,
) -> str:
    """
    Get system prompt by language, output mode, and prompt version.

    Args:
        lang: Language code, 'cn' for Chinese, 'en' for English.
        output_mode: Model output mode, one of text_dsl/json_schema/tool_calls/auto.
        prompt_version: Prompt renderer version. The default context_harness_v1 uses
            a single output contract per mode; legacy_text_dsl preserves the old
            exported text_dsl prompt for rollback.

    Returns:
        System prompt string.
    """
    normalized_version = get_prompt_version(prompt_version)
    if normalized_version == LEGACY_PROMPT_VERSION:
        return LEGACY_SYSTEM_PROMPT_EN if lang == "en" else LEGACY_SYSTEM_PROMPT_ZH
    base_prompt = BASE_SYSTEM_PROMPT_EN if lang == "en" else BASE_SYSTEM_PROMPT_ZH
    return "\n\n".join([base_prompt, _output_contract(lang, output_mode)])


# Default to Chinese for backward compatibility. `prompts.py` is retained as a
# compatibility wrapper; active prompt rendering goes through get_system_prompt().
SYSTEM_PROMPT = SYSTEM_PROMPT_ZH

__all__ = [
    "APP_PACKAGES",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_ZH",
    "SYSTEM_PROMPT_EN",
    "PROMPT_VERSION",
    "LEGACY_PROMPT_VERSION",
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
