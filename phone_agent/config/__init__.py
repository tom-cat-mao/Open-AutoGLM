"""Configuration module for Phone Agent."""

from phone_agent.config.apps import APP_PACKAGES
from phone_agent.config.i18n import get_message, get_messages
from phone_agent.config.prompts_en import SYSTEM_PROMPT as SYSTEM_PROMPT_EN
from phone_agent.config.prompts_zh import SYSTEM_PROMPT as SYSTEM_PROMPT_ZH
from phone_agent.config.timing import (
    TIMING_CONFIG,
    ActionTimingConfig,
    ConnectionTimingConfig,
    DeviceTimingConfig,
    TimingConfig,
    get_timing_config,
    update_timing_config,
)

VALID_OUTPUT_MODES = {"text_dsl", "json_schema", "tool_calls", "auto"}


def _output_mode_prompt_suffix(lang: str, output_mode: str) -> str:
    """Return output-mode-specific prompt instructions."""
    if output_mode not in VALID_OUTPUT_MODES:
        raise ValueError(
            "output_mode must be one of: text_dsl, json_schema, tool_calls, auto"
        )
    if output_mode == "text_dsl":
        return ""
    if lang == "en":
        if output_mode == "json_schema":
            return """

# Output mode override: JSON schema
For this run, ignore the XML <think>/<answer> wrapper requirement above. Return exactly one JSON object and no Markdown/code fences.
Supported shapes:
- {"type":"do","action":"tap","x":500,"y":500}
- {"type":"do","action":"tap","x":500,"y":500,"message":"sensitive operation"}
- {"type":"do","action":"swipe","start":[500,800],"end":[500,200]}
- {"type":"do","action":"type","text":"hello"}
- {"type":"do","action":"launch","app":"Settings"}
- {"type":"do","action":"wait","duration":"1 seconds"}
- {"type":"do","action":"back"} / {"type":"do","action":"home"} / {"type":"do","action":"take_over","message":"login required"}
- {"type":"finish","message":"Task completed"}
Coordinates must stay in the 0-1000 relative coordinate system.
"""
        if output_mode == "tool_calls":
            return """

# Output mode override: tool calls
For this run, use the provided function/tool call interface to emit exactly one action. Do not put the action in plain text or Markdown. Use the `do` tool for phone actions and the `finish` tool when the task is complete. Coordinates must stay in the 0-1000 relative coordinate system.
"""
        if output_mode == "auto":
            return """

# Output mode note: auto
The agent can parse text DSL, JSON objects, or provider tool calls. Prefer the XML text DSL format described above unless the provider has been configured to emit JSON or tool calls. Coordinates must stay in the 0-1000 relative coordinate system.
"""
        return ""

    if output_mode == "json_schema":
        return """

# 输出模式覆盖：JSON schema
本次运行请忽略上方 XML <think>/<answer> 包裹格式要求。你必须只返回一个 JSON 对象，不要输出 Markdown 或代码块。
支持的格式：
- {"type":"do","action":"tap","x":500,"y":500}
- {"type":"do","action":"tap","x":500,"y":500,"message":"敏感操作说明"}
- {"type":"do","action":"swipe","start":[500,800],"end":[500,200]}
- {"type":"do","action":"type","text":"你好"}
- {"type":"do","action":"launch","app":"设置"}
- {"type":"do","action":"wait","duration":"1 seconds"}
- {"type":"do","action":"back"} / {"type":"do","action":"home"} / {"type":"do","action":"take_over","message":"需要登录"}
- {"type":"finish","message":"任务已完成"}
坐标必须保持 0-1000 相对坐标，不要输出绝对像素坐标。
"""
    if output_mode == "tool_calls":
        return """

# 输出模式覆盖：tool calls
本次运行请使用 provider 提供的 function/tool call 接口输出且只输出一个动作。不要把动作写在普通文本、Markdown 或 <answer> 中。手机动作使用 `do` tool，任务完成使用 `finish` tool。坐标必须保持 0-1000 相对坐标。
"""
    if output_mode == "auto":
        return """

# 输出模式说明：auto
当前解析器可以识别 text DSL、JSON 对象或 provider tool calls。除非 provider 已配置为 JSON/tool calls，否则优先使用上方 XML text DSL 格式。坐标必须保持 0-1000 相对坐标。
"""
    return ""


def get_system_prompt(lang: str = "cn", output_mode: str = "text_dsl") -> str:
    """
    Get system prompt by language.

    Args:
        lang: Language code, 'cn' for Chinese, 'en' for English.
        output_mode: Model output mode, one of text_dsl/json_schema/tool_calls/auto.

    Returns:
        System prompt string.
    """
    base_prompt = SYSTEM_PROMPT_EN if lang == "en" else SYSTEM_PROMPT_ZH
    return base_prompt + _output_mode_prompt_suffix(lang, output_mode)


# Default to Chinese for backward compatibility
SYSTEM_PROMPT = SYSTEM_PROMPT_ZH

__all__ = [
    "APP_PACKAGES",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_ZH",
    "SYSTEM_PROMPT_EN",
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
