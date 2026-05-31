# Open-AutoGLM Agent Guide

## Core Loop

`Screenshot -> VLM inference -> Parse action -> Execute on device -> Reflect -> Repeat`

项目核心循环由 LangGraph `StateGraph` 实现；详细 roadmap 与阶段状态按需读取 `.trae/rules/graph.mdc`。

## Non-Negotiable Constraints

| 约束 | 要求 |
|---|---|
| 坐标 | 模型输出 0-1000 相对坐标；tool 内必须用 `convert_relative_to_absolute()` 转绝对像素 |
| 动作解析 | 必须用 `ast.parse` + `ast.literal_eval`；禁止 `eval()` |
| 图片上下文 | 每步后必须用 `MessageBuilder.remove_images_from_message()` 剥离历史图片 |
| HITL | 支付/隐私走 `confirm_node`；登录/验证码走 `takeover_node`；均使用 LangGraph `interrupt()` |
| 设备抽象 | 设备操作统一经 `DeviceFactory` -> `phone_agent/adb/` |
| messages reducer | `plan_node` 只返回新增消息；`execute_node` 返回完整重建列表，避免 token 爆炸 |
| confirm-then-execute | confirm 接受敏感 Tap 后路由到 `execute`；`pending_execute` 分支不得再次 `_strip_and_append` |
| Tool DI | `execute_node` 从 graph config 注入 `device_factory`；tool schema 不得暴露 `device_factory` |
| Trace | 默认本地 JSONL trace；`RunResult.trace_id/trace_path` 与 eval JSON 可关联 `.traces/{trace_id}.jsonl`；敏感截图/API key/隐私文本默认脱敏 |
| Reflection | `reflect_node` 维护 `reflection_verdict/failure_cause/suggested_strategy`；Plan 下一轮必须能读取结构化失败原因和策略 |

## Key Paths

| 范围 | 路径 |
|---|---|
| CLI 入口 | `main.py` |
| Agent | `phone_agent/agent.py` |
| Graph | `phone_agent/graph/{state.py,builder.py,edges.py,trace.py,nodes/,tools/}` |
| Actions | `phone_agent/actions/handler.py` |
| Model | `phone_agent/model/client.py` |
| Device | `phone_agent/device_factory.py`, `phone_agent/adb/` |
| Prompts | `phone_agent/config/prompts.py`, `prompts_zh.py`, `prompts_en.py` |

## Rule Loading Policy

| 任务 | 读取规则 |
|---|---|
| LangGraph / Agent loop / HITL / Tool 变更 | `.trae/rules/graph.mdc` + `.trae/rules/architecture.mdc` |
| roadmap / 实现计划 / 架构审查 | `.trae/rules/design-loop.mdc` |
| Python 代码风格或测试 | `.trae/rules/style.mdc` |
| 配置、模型、设备、动作系统 | 对应 `.trae/rules/{config,model,devices,actions}.mdc` |
| 普通问答或轻量改动 | 不主动加载重型规则 |

## Commands & Validation

| 场景 | 命令 |
|---|---|
| 安装开发依赖 | `.venv/bin/pip install -e ".[dev]"` |
| 全量测试 | `.venv/bin/pytest` |
| Graph 测试 | `.venv/bin/pytest tests/graph -v` |
| Eval/Trace 测试 | `.venv/bin/pytest tests/evals tests/graph/test_trace.py -v` |
| Dry-run eval trace | `.venv/bin/python evals/run_eval.py --dry-run --trace-dir .traces/smoke` |
| 部署检查 | `.venv/bin/python scripts/check_deployment_cn.py` 或 `check_deployment_en.py` |

## Version Management

- Phase 完成后按项目规范更新 `.trae/rules/graph.mdc`，若改动架构/API/评测/trace，同步更新 `README.md`、`docs/future-roadmap.md` 与本文件；commit message：`feat(graph): <phase 目标>`。
- 禁止对 `main` 和 `feature/langgraph-refactor` 执行 `git push --force`。
- 未经用户明确要求，不主动创建 commit。

## Compact Instructions

压缩时保留：当前任务与进度、Non-Negotiable Constraints、Key Paths、Phase 8 trace 现状、必要 roadmap 状态与未完成 TODO。
