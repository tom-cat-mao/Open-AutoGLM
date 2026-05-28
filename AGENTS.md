# Open-AutoGLM Agent Guide

## Core Loop (MUST READ)

```
Screenshot -> VLM inference (thinking + action) -> Parse action -> Execute on device -> Repeat
```

Every component exists to serve this loop.

## Global Constraints (MUST follow before ANY code change)

1. **Coordinate system**: Model outputs 0-1000 relative coordinates. Action handlers MUST convert to absolute pixels via `_convert_relative_to_absolute()`. Never pass raw model coordinates to device commands.
2. **Action parsing safety**: MUST use `ast.parse` + `ast.literal_eval`. NEVER use `eval()`. See `phone_agent/actions/handler.py:parse_action()`.
3. **Image context management**: After each step, images MUST be stripped from conversation history via `MessageBuilder.remove_images_from_message()`. This prevents token overflow.
4. **Security callbacks**: Sensitive operations (payment, privacy) MUST go through `confirmation_callback`. Login/captcha MUST go through `takeover_callback`. Both have console defaults but are overridable.
5. **Device abstraction**: All device operations go through `DeviceFactory` -> `phone_agent/adb/` module. Single platform, single code path.

## Architecture at a Glance

```
main.py                          # CLI entry: arg parsing, system checks, agent creation
phone_agent/
├── agent.py                     # PhoneAgent (Android)
├── device_factory.py            # DeviceFactory: loads adb module
├── model/
│   └── client.py                # ModelClient (OpenAI streaming), ModelConfig, MessageBuilder
├── actions/
│   └── handler.py               # ActionHandler + parse_action()
├── adb/                         # Android device control
└── config/
    ├── apps.py
    ├── prompts.py / prompts_zh.py / prompts_en.py
    ├── i18n.py
    └── timing.py
```

## Quick Reference

- **Entry**: `main.py`
- **Agent**: `phone_agent/agent.py:PhoneAgent`
- **Actions**: `phone_agent/actions/handler.py`
- **Model**: `phone_agent/model/client.py:ModelClient`
- **Device**: `phone_agent/device_factory.py:DeviceFactory`
- **Prompts**: `phone_agent/config/prompts.py` (CN), `phone_agent/config/prompts_en.py` (EN)

## Compact Instructions

压缩时始终保留：
- 当前正在执行的任务描述和进度
- Global Constraints 中的 5 条不变量
- Architecture at a Glance 中的目录结构
